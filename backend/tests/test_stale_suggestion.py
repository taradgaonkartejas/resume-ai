"""A suggestion must not overwrite work done after it was generated.

`apply_patch` only checks that the target_ref still *resolves*. It does not
check that the text at that ref is still what the suggestion was written
against, so accepting a stale suggestion silently destroyed a later hand-edit.
"""

import pytest

BULLET = "Built Terraform modules adopted by 8 teams."
MINE = "My own carefully written bullet that took ten minutes."

RESUME = {
    "contact": {"name": "Jane Doe", "email": "jane@example.com"},
    "summary": {"text": "Platform engineer with eight years of experience."},
    "experience": [
        {
            "company": "Acme Corp",
            "role": "Senior Engineer",
            "dates": "2020-2024",
            "bullets": [BULLET, "Led the platform migration."],
        }
    ],
    "skills": [{"label": "Platform", "items": ["Terraform", "AWS"]}],
    "education": [],
    "projects": [],
}


@pytest.fixture()
def headers(priya):
    return {"X-User-Id": priya}


@pytest.fixture()
def tailored(client, headers):
    """A forked resume with at least one pending suggestion."""
    made = client.post("/api/resumes", json={"title": "Stale probe"}, headers=headers).json()
    client.put(
        f"/api/resumes/{made['id']}/data",
        json={"structured_data": RESUME},
        headers=headers,
    )
    session = client.post(
        f"/api/resumes/{made['id']}/tailor",
        json={
            "jd_title": "SRE",
            "jd_content": "Kubernetes Terraform AWS observability on-call",
            "fork": True,
        },
        headers=headers,
    ).json()
    suggestions = client.get(
        f"/api/resumes/{session['resume_id']}/tailor/{session['id']}/suggestions",
        headers=headers,
    ).json()["active"]
    assert suggestions, "fixture needs at least one suggestion"
    return session["resume_id"], suggestions


def _edit_target(client, headers, resume_id, target_ref, text):
    doc = client.get(f"/api/resumes/{resume_id}", headers=headers).json()["structured_data"]
    section, field = target_ref.split(".")
    if section.startswith("exp_"):
        i = int(section.split("_")[1])
        j = int(field.split("_")[1])
        doc["experience"][i]["bullets"][j] = text
    else:
        doc["summary"]["text"] = text
    client.put(
        f"/api/resumes/{resume_id}/data", json={"structured_data": doc}, headers=headers
    )
    return doc


def test_accepting_a_stale_suggestion_is_a_conflict(client, headers, tailored):
    resume_id, suggestions = tailored
    sug = suggestions[0]
    _edit_target(client, headers, resume_id, sug["target_ref"], MINE)

    resp = client.patch(f"/api/suggestions/{sug['id']}", json={"action": "accept"}, headers=headers)
    assert resp.status_code == 409
    assert "changed" in resp.json()["detail"].lower()


def test_the_later_edit_survives(client, headers, tailored):
    """The point of the guard: user work is not destroyed."""
    resume_id, suggestions = tailored
    sug = suggestions[0]
    _edit_target(client, headers, resume_id, sug["target_ref"], MINE)
    client.patch(f"/api/suggestions/{sug['id']}", json={"action": "accept"}, headers=headers)

    doc = client.get(f"/api/resumes/{resume_id}", headers=headers).json()["structured_data"]
    section, field = sug["target_ref"].split(".")
    if section.startswith("exp_"):
        i, j = int(section.split("_")[1]), int(field.split("_")[1])
        assert doc["experience"][i]["bullets"][j] == MINE
    else:
        assert doc["summary"]["text"] == MINE


def test_a_conflicted_suggestion_stays_pending(client, headers, tailored):
    """409 must not consume the suggestion -- the user can still reject it."""
    resume_id, suggestions = tailored
    sug = suggestions[0]
    _edit_target(client, headers, resume_id, sug["target_ref"], MINE)
    client.patch(f"/api/suggestions/{sug['id']}", json={"action": "accept"}, headers=headers)

    rejected = client.patch(
        f"/api/suggestions/{sug['id']}", json={"action": "reject"}, headers=headers
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_an_untouched_suggestion_still_applies(client, headers, tailored):
    resume_id, suggestions = tailored
    resp = client.patch(
        f"/api/suggestions/{suggestions[0]['id']}", json={"action": "accept"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


def test_editing_the_suggestion_does_not_make_it_stale(client, headers, tailored):
    """Edit changes the SUGGESTION's text, not the resume's, so accept works."""
    resume_id, suggestions = tailored
    sug = suggestions[0]
    edited = client.patch(
        f"/api/suggestions/{sug['id']}",
        json={"action": "edit", "edited_text": "Reworded by me, same facts."},
        headers=headers,
    )
    assert edited.status_code == 200
    accepted = client.patch(
        f"/api/suggestions/{sug['id']}", json={"action": "accept"}, headers=headers
    )
    assert accepted.status_code == 200


def test_reverting_the_edit_clears_the_conflict(client, headers, tailored):
    """Put the original text back and the suggestion applies again."""
    resume_id, suggestions = tailored
    sug = suggestions[0]
    _edit_target(client, headers, resume_id, sug["target_ref"], MINE)
    assert (
        client.patch(
            f"/api/suggestions/{sug['id']}", json={"action": "accept"}, headers=headers
        ).status_code
        == 409
    )

    _edit_target(client, headers, resume_id, sug["target_ref"], sug["original_text"])
    assert (
        client.patch(
            f"/api/suggestions/{sug['id']}", json={"action": "accept"}, headers=headers
        ).status_code
        == 200
    )


def test_conflict_is_409_not_422(client, headers, tailored):
    """A stale suggestion is a state conflict, not a malformed request."""
    resume_id, suggestions = tailored
    sug = suggestions[0]
    _edit_target(client, headers, resume_id, sug["target_ref"], MINE)
    resp = client.patch(f"/api/suggestions/{sug['id']}", json={"action": "accept"}, headers=headers)
    assert resp.status_code == 409

    bad = client.patch(f"/api/suggestions/{sug['id']}", json={"action": "nope"}, headers=headers)
    assert bad.status_code == 422
