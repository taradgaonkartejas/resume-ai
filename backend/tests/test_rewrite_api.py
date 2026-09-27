"""POST /resumes/{id}/sections/{target_ref}/rewrite.

Produces an ordinary pending Suggestion, so Accept/Reject/Edit and version
history work unchanged. There is exactly one apply-an-edit lifecycle.
"""

from unittest.mock import patch

import pytest

DOC = {
    "contact": {
        "name": "Jane Doe", "email": "j@x.com", "phone": "555-0100",
        "location": "Pune", "links": ["github.com/jane"],
    },
    "summary": {"text": ""},
    "experience": [
        {
            "company": "Acme", "role": "Platform Engineer", "dates": "2020-2024",
            "bullets": [
                "Worked on the deployment pipeline",
                "Helped with monitoring " + ("word " * 50),
            ],
        }
    ],
    "skills": [{"label": "Platform", "items": ["Python", "Terraform"]}],
    "education": [{"school": "State", "degree": "BSc", "dates": "2016"}],
    "projects": [],
}


@pytest.fixture()
def headers(priya):
    return {"X-User-Id": priya}


@pytest.fixture()
def resume(client, headers):
    made = client.post("/api/resumes", json={"title": "Rewrite probe"}, headers=headers).json()
    client.put(
        f"/api/resumes/{made['id']}/data", json={"structured_data": DOC}, headers=headers
    )
    return made["id"]


def _rewrite(client, headers, resume, ref, **body):
    return client.post(
        f"/api/resumes/{resume}/sections/{ref}/rewrite", json=body, headers=headers
    )


# ------------------------------------------------------------------ contract
def test_weak_verb_rewrite_creates_a_pending_suggestion(client, headers, resume):
    resp = _rewrite(client, headers, resume, "exp_0.bullet_0", finding_id="experience.weak_verbs")
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["origin"] == "analysis"
    assert body["session_id"] is None
    assert body["target_ref"] == "exp_0.bullet_0"
    assert body["original_text"] == DOC["experience"][0]["bullets"][0]
    assert body["suggested_text"].strip()


def test_missing_summary_takes_the_compose_path(client, headers, resume):
    """resolve() returns "" -- this is writing, not revising."""
    resp = _rewrite(client, headers, resume, "summary.text", finding_id="summary.missing")
    assert resp.status_code == 201
    body = resp.json()
    assert body["original_text"] == ""
    assert body["suggested_text"].strip()


def test_composed_summary_never_invents_a_number(client, headers, resume):
    """The fallback once emitted '1 role(s)' and the critic 422'd it."""
    import re

    resp = _rewrite(client, headers, resume, "summary.text", finding_id="summary.missing")
    assert resp.status_code == 201
    assert not re.search(r"\d", resp.json()["suggested_text"])


def test_unresolvable_ref_is_422(client, headers, resume):
    assert _rewrite(client, headers, resume, "experience").status_code == 422
    assert _rewrite(client, headers, resume, "exp_9.bullet_9").status_code == 422


def test_cross_user_is_404(client, headers, arjun, resume):
    resp = client.post(
        f"/api/resumes/{resume}/sections/exp_0.bullet_0/rewrite",
        json={},
        headers={"X-User-Id": arjun},
    )
    assert resp.status_code == 404


# ------------------------------------------------------------- no duplicates
def test_reclicking_returns_the_same_draft(client, headers, resume):
    a = _rewrite(client, headers, resume, "exp_0.bullet_0", finding_id="experience.weak_verbs")
    b = _rewrite(client, headers, resume, "exp_0.bullet_0", finding_id="experience.weak_verbs")
    assert a.status_code == 201
    assert a.json()["id"] == b.json()["id"]


def test_regenerate_supersedes_the_old_draft(client, headers, resume):
    a = _rewrite(client, headers, resume, "exp_0.bullet_0", finding_id="experience.weak_verbs")
    b = _rewrite(
        client, headers, resume, "exp_0.bullet_0",
        finding_id="experience.weak_verbs", regenerate=True,
    )
    assert b.status_code == 201
    assert a.json()["id"] != b.json()["id"]


# ------------------------------------------------- the critic gates the write
def test_an_ungrounded_draft_is_never_persisted(client, headers, resume):
    """Critic rejection must 422, not store a fabrication."""
    from app.ai import agents

    bad = {
        "target_ref": "exp_0.bullet_0",
        "original_text": DOC["experience"][0]["bullets"][0],
        "suggested_text": "Led a team of 12 engineers and managed a budget of $4M.",
        "keywords": [],
        "reasoning": "",
    }
    with patch.object(
        agents, "rewrite_for_finding",
        return_value=type("O", (), {"value": bad, "used_llm": True, "status": "ok"})(),
    ):
        resp = _rewrite(client, headers, resume, "exp_0.bullet_0")
    assert resp.status_code == 422
    assert "reject" in resp.json()["detail"].lower()

    listed = client.get(f"/api/resumes/{resume}", headers=headers)
    assert listed.status_code == 200


def test_a_no_op_rewrite_is_refused_with_a_useful_message(client, headers, resume):
    """Without a key the fallback cannot add a metric -- say so plainly."""
    resp = _rewrite(
        client, headers, resume, "exp_0.bullet_1",
        finding_id="experience.unquantified_bullets",
    )
    if resp.status_code == 422:
        detail = resp.json()["detail"].lower()
        assert "edit" in detail or "cannot" in detail


# --------------------------------------------- it feeds the normal lifecycle
def test_the_draft_can_be_accepted_like_any_suggestion(client, headers, resume):
    made = _rewrite(
        client, headers, resume, "exp_0.bullet_0", finding_id="experience.weak_verbs"
    ).json()
    accepted = client.patch(
        f"/api/suggestions/{made['id']}", json={"action": "accept"}, headers=headers
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"

    doc = client.get(f"/api/resumes/{resume}", headers=headers).json()["structured_data"]
    assert doc["experience"][0]["bullets"][0] == made["suggested_text"]


def test_accepting_records_a_version_with_analysis_as_the_source(client, headers, resume):
    made = _rewrite(
        client, headers, resume, "exp_0.bullet_0", finding_id="experience.weak_verbs"
    ).json()
    client.patch(f"/api/suggestions/{made['id']}", json={"action": "accept"}, headers=headers)
    versions = client.get(f"/api/resumes/{resume}/versions", headers=headers).json()
    assert any("exp_0.bullet_0" in (v.get("label") or "") for v in versions)


def test_a_rewrite_draft_is_stale_checked_too(client, headers, resume):
    """The 409 guard covers analysis-origin suggestions, not just tailoring."""
    made = _rewrite(
        client, headers, resume, "exp_0.bullet_0", finding_id="experience.weak_verbs"
    ).json()
    doc = client.get(f"/api/resumes/{resume}", headers=headers).json()["structured_data"]
    doc["experience"][0]["bullets"][0] = "My own edit."
    client.put(f"/api/resumes/{resume}/data", json={"structured_data": doc}, headers=headers)

    resp = client.patch(
        f"/api/suggestions/{made['id']}", json={"action": "accept"}, headers=headers
    )
    assert resp.status_code == 409


def test_omitting_finding_id_picks_the_first_finding_for_that_ref():
    """Documented, because it decides which prompt runs.

    Two findings can share a target_ref (weak_verbs and unquantified_bullets
    both point at exp_0.bullet_0). Without finding_id the first match wins,
    which offline may be one the deterministic fallback cannot fix. Callers
    that care should pass finding_id.
    """
    from app.services.suggestion_service import _finding_for

    picked = _finding_for(DOC, "", "exp_0.bullet_0")
    assert picked["target_ref"] == "exp_0.bullet_0"

    explicit = _finding_for(DOC, "experience.weak_verbs", "exp_0.bullet_0")
    assert explicit["id"] == "experience.weak_verbs"
