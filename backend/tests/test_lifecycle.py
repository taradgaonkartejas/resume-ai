"""Suggestion lifecycle and version cursor — the multi-table transaction."""

from app.services import resume_ops
from tests.conftest import new_resume

RESUME = {
    "contact": {"name": "Priya Sharma", "email": "p@example.com"},
    "summary": {"text": "SRE with six years of production experience."},
    "experience": [
        {
            "company": "Acme",
            "role": "SRE",
            "dates": "2021 - Present",
            "bullets": [
                "Led migration of 40 services to Kubernetes",
                "Reduced pager volume by 45%",
            ],
        }
    ],
    "projects": [],
    "education": [],
    "skills": [{"label": "Platform", "items": ["Kubernetes"]}],
}

JD = {"jd_title": "SRE", "jd_content": "Terraform Prometheus GitOps AWS Grafana"}


def _start(client, headers, resume_id):
    session = client.post(
        f"/api/resumes/{resume_id}/tailor", headers=headers, json=JD
    ).json()
    buckets = client.get(
        f"/api/resumes/{resume_id}/tailor/{session['id']}/suggestions", headers=headers
    ).json()
    return session, buckets


def test_accept_applies_text_and_bumps_version(client, priya):
    resume_id = new_resume(client, priya, "Lifecycle", RESUME)
    headers = {"X-User-Id": priya}
    _, buckets = _start(client, headers, resume_id)
    suggestion = buckets["active"][0]

    before = client.get(f"/api/resumes/{resume_id}", headers=headers).json()
    client.patch(
        f"/api/suggestions/{suggestion['id']}", headers=headers, json={"action": "accept"}
    )
    after = client.get(f"/api/resumes/{resume_id}", headers=headers).json()

    assert after["version_cursor"] == before["version_cursor"] + 1
    landed = resume_ops.resolve(after["structured_data"], suggestion["target_ref"])
    assert landed == suggestion["suggested_text"]


def test_reject_leaves_resume_untouched(client, priya):
    resume_id = new_resume(client, priya, "Lifecycle", RESUME)
    headers = {"X-User-Id": priya}
    _, buckets = _start(client, headers, resume_id)
    suggestion = buckets["active"][0]

    before = client.get(f"/api/resumes/{resume_id}", headers=headers).json()
    client.patch(
        f"/api/suggestions/{suggestion['id']}", headers=headers, json={"action": "reject"}
    )
    after = client.get(f"/api/resumes/{resume_id}", headers=headers).json()

    assert after["version_cursor"] == before["version_cursor"]
    assert after["structured_data"] == before["structured_data"]


def test_edit_then_accept_applies_edited_text(client, priya):
    resume_id = new_resume(client, priya, "Lifecycle", RESUME)
    headers = {"X-User-Id": priya}
    _, buckets = _start(client, headers, resume_id)
    suggestion = buckets["active"][0]

    custom = "Completely custom replacement text."
    client.patch(
        f"/api/suggestions/{suggestion['id']}",
        headers=headers,
        json={"action": "edit", "edited_text": custom},
    )
    client.patch(
        f"/api/suggestions/{suggestion['id']}", headers=headers, json={"action": "accept"}
    )

    after = client.get(f"/api/resumes/{resume_id}", headers=headers).json()
    assert resume_ops.resolve(after["structured_data"], suggestion["target_ref"]) == custom


def test_double_accept_is_rejected(client, priya):
    resume_id = new_resume(client, priya, "Lifecycle", RESUME)
    headers = {"X-User-Id": priya}
    _, buckets = _start(client, headers, resume_id)
    suggestion = buckets["active"][0]

    first = client.patch(
        f"/api/suggestions/{suggestion['id']}", headers=headers, json={"action": "accept"}
    )
    second = client.patch(
        f"/api/suggestions/{suggestion['id']}", headers=headers, json={"action": "accept"}
    )
    assert first.status_code == 200
    assert second.status_code == 422


def test_accept_recomputes_match_score(client, priya):
    resume_id = new_resume(client, priya, "Lifecycle", RESUME)
    headers = {"X-User-Id": priya}
    session, buckets = _start(client, headers, resume_id)
    baseline = session["baseline_percent"]

    client.patch(
        f"/api/suggestions/{buckets['active'][0]['id']}",
        headers=headers,
        json={"action": "accept"},
    )
    sessions = client.get(f"/api/resumes/{resume_id}/tailor", headers=headers).json()
    updated = next(s for s in sessions if s["id"] == session["id"])
    assert updated["match_percent"] >= baseline


def test_undo_redo_walks_the_cursor(client, priya):
    resume_id = new_resume(client, priya, "Versions", RESUME)
    headers = {"X-User-Id": priya}

    for text in ("First edit.", "Second edit."):
        data = client.get(f"/api/resumes/{resume_id}", headers=headers).json()["structured_data"]
        data["summary"]["text"] = text
        client.put(
            f"/api/resumes/{resume_id}/data", headers=headers, json={"structured_data": data}
        )

    at_head = client.get(f"/api/resumes/{resume_id}", headers=headers).json()
    assert at_head["structured_data"]["summary"]["text"] == "Second edit."

    undone = client.post(f"/api/resumes/{resume_id}/undo", headers=headers).json()
    assert undone["structured_data"]["summary"]["text"] == "First edit."

    redone = client.post(f"/api/resumes/{resume_id}/redo", headers=headers).json()
    assert redone["structured_data"]["summary"]["text"] == "Second edit."


def test_undo_at_floor_and_redo_at_head_are_errors(client, priya):
    resume_id = new_resume(client, priya, "Bounds", RESUME)
    headers = {"X-User-Id": priya}
    data = client.get(f"/api/resumes/{resume_id}", headers=headers).json()["structured_data"]
    data["summary"]["text"] = "Only edit."
    client.put(f"/api/resumes/{resume_id}/data", headers=headers, json={"structured_data": data})

    assert client.post(f"/api/resumes/{resume_id}/redo", headers=headers).status_code == 422
    client.post(f"/api/resumes/{resume_id}/undo", headers=headers)
    assert client.post(f"/api/resumes/{resume_id}/undo", headers=headers).status_code == 422


def test_edit_while_behind_head_truncates_redo(client, priya):
    resume_id = new_resume(client, priya, "Truncate", RESUME)
    headers = {"X-User-Id": priya}

    for text in ("v2.", "v3."):
        data = client.get(f"/api/resumes/{resume_id}", headers=headers).json()["structured_data"]
        data["summary"]["text"] = text
        client.put(
            f"/api/resumes/{resume_id}/data", headers=headers, json={"structured_data": data}
        )

    client.post(f"/api/resumes/{resume_id}/undo", headers=headers)
    data = client.get(f"/api/resumes/{resume_id}", headers=headers).json()["structured_data"]
    data["summary"]["text"] = "branch."
    client.put(f"/api/resumes/{resume_id}/data", headers=headers, json={"structured_data": data})

    # The old v3 is gone: redo must now fail.
    assert client.post(f"/api/resumes/{resume_id}/redo", headers=headers).status_code == 422


def test_chat_emits_an_acceptable_suggestion(client, priya):
    resume_id = new_resume(client, priya, "Chat", RESUME)
    headers = {"X-User-Id": priya}

    reply = client.post(
        f"/api/resumes/{resume_id}/chat",
        headers=headers,
        json={"content": "Make my summary more impactful"},
    ).json()

    assert reply["suggestion"] is not None
    assert reply["suggestion"]["origin"] == "chat"
    assert reply["tokens_left"] == 24

    client.patch(
        f"/api/suggestions/{reply['suggestion']['id']}",
        headers=headers,
        json={"action": "accept"},
    )
    after = client.get(f"/api/resumes/{resume_id}", headers=headers).json()
    assert after["structured_data"]["summary"]["text"] == reply["suggestion"]["suggested_text"]
