"""Acceptance gate 4 — the critic drops ungrounded drafts.

A suggestion whose target_ref does not resolve, or whose original_text does not
match the resume verbatim, must never be persisted or applied.
"""

import uuid

import pytest

from app.services import resume_ops
from app.services.exceptions import InvalidTargetRef
from tests.conftest import new_resume

RESUME = {
    "contact": {"name": "Priya Sharma", "email": "p@example.com"},
    "summary": {"text": "SRE with six years of production experience."},
    "experience": [
        {
            "company": "Acme",
            "role": "SRE",
            "dates": "2021 - Present",
            "bullets": ["Led migration of 40 services to Kubernetes"],
        }
    ],
    "projects": [],
    "education": [],
    "skills": [{"label": "Platform", "items": ["Kubernetes"]}],
}


def test_resolve_valid_refs():
    assert resume_ops.resolve(RESUME, "summary.text").startswith("SRE with six")
    assert "Kubernetes" in resume_ops.resolve(RESUME, "exp_0.bullet_0")
    assert resume_ops.resolve(RESUME, "skills.0.items") == "Kubernetes"


def test_nonexistent_refs_raise():
    for ref in ("exp_9.bullet_0", "exp_0.bullet_7", "skills.4.items", "nonsense"):
        with pytest.raises(InvalidTargetRef):
            resume_ops.resolve(RESUME, ref)
        assert resume_ops.exists(RESUME, ref) is False


def test_patch_rejects_unresolvable_ref():
    with pytest.raises(InvalidTargetRef):
        resume_ops.apply_patch(RESUME, "exp_9.bullet_0", "text")


def test_patch_does_not_mutate_input():
    patched = resume_ops.apply_patch(RESUME, "summary.text", "Rewritten.")
    assert patched["summary"]["text"] == "Rewritten."
    assert RESUME["summary"]["text"] == "SRE with six years of production experience."


def test_generated_suggestions_are_all_grounded(client, priya):
    resume_id = new_resume(client, priya, "Grounding", RESUME)
    headers = {"X-User-Id": priya}
    session = client.post(
        f"/api/resumes/{resume_id}/tailor",
        headers=headers,
        json={"jd_title": "SRE", "jd_content": "Terraform Prometheus GitOps AWS"},
    ).json()
    buckets = client.get(
        f"/api/resumes/{resume_id}/tailor/{session['id']}/suggestions",
        headers=headers,
    ).json()

    data = client.get(f"/api/resumes/{resume_id}", headers=headers).json()["structured_data"]
    for suggestion in buckets["active"]:
        assert suggestion["grounded"] is True
        assert resume_ops.exists(data, suggestion["target_ref"])
        # original_text must match the resume verbatim
        assert resume_ops.resolve(data, suggestion["target_ref"]) == suggestion["original_text"]


def test_accept_fails_when_target_vanishes(client, priya):
    """If the section is deleted after generation, accept must not silently pass."""
    resume_id = new_resume(client, priya, "Vanishing", RESUME)
    headers = {"X-User-Id": priya}
    session = client.post(
        f"/api/resumes/{resume_id}/tailor",
        headers=headers,
        json={"jd_title": "SRE", "jd_content": "Terraform Prometheus GitOps AWS"},
    ).json()
    buckets = client.get(
        f"/api/resumes/{resume_id}/tailor/{session['id']}/suggestions",
        headers=headers,
    ).json()
    target = next(
        (s for s in buckets["active"] if s["target_ref"].startswith("exp_")), None
    )
    if target is None:
        pytest.skip("no experience-targeted suggestion generated")

    stripped = dict(RESUME)
    stripped["experience"] = []
    client.put(
        f"/api/resumes/{resume_id}/data",
        headers=headers,
        json={"structured_data": stripped},
    )

    response = client.patch(
        f"/api/suggestions/{target['id']}", headers=headers, json={"action": "accept"}
    )
    assert response.status_code in (400, 422)


def test_ungrounded_suggestion_is_never_persisted(client, priya):
    """Directly assert the critic rule the writer must satisfy."""
    resume_id = new_resume(client, priya, "Critic", RESUME)
    headers = {"X-User-Id": priya}
    session = client.post(
        f"/api/resumes/{resume_id}/tailor",
        headers=headers,
        json={"jd_title": "SRE", "jd_content": "Terraform Prometheus"},
    ).json()
    buckets = client.get(
        f"/api/resumes/{resume_id}/tailor/{session['id']}/suggestions",
        headers=headers,
    ).json()
    refs = [s["target_ref"] for s in buckets["active"]]
    assert all(resume_ops.exists(RESUME, ref) for ref in refs)
    assert not any(ref.startswith("exp_9") for ref in refs)


def test_suggestion_id_must_exist(client, priya):
    response = client.patch(
        f"/api/suggestions/{uuid.uuid4()}",
        headers={"X-User-Id": priya},
        json={"action": "accept"},
    )
    assert response.status_code == 404
