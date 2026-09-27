"""GET /resumes/{id}/analysis/steps — the guided editor's data source."""

from tests.conftest import new_resume

WEAK = {
    "contact": {"name": "Sam Rivera", "headline": "Engineer", "email": "",
                "phone": "", "location": "", "links": []},
    "summary": {"text": "Engineer."},
    "experience": [{
        "company": "Acme", "role": "Engineer", "dates": "",
        "bullets": ["Responsible for various backend tasks and maintenance"],
    }],
    "projects": [],
    "education": [],
    "skills": [],
}


def _steps(client, user_id, resume_id):
    r = client.get(f"/api/resumes/{resume_id}/analysis/steps",
                   headers={"X-User-Id": user_id})
    assert r.status_code == 200, r.text
    return r.json()


def test_steps_returns_five_ordered_steps(client, priya, priya_resume):
    body = _steps(client, priya, priya_resume)
    assert [s["id"] for s in body["steps"]] == [
        "contact", "summary", "experience", "format", "extras",
    ]
    # The fifth step adds 0 to the denominator, so this still holds.
    assert body["max_score"] == 100
    assert sum(s["max"] for s in body["steps"]) == 100
    assert body["resume_id"] == priya_resume


def test_extras_step_is_optional_and_zeroed_over_the_wire(client, priya, priya_resume):
    step = next(s for s in _steps(client, priya, priya_resume)["steps"]
                if s["id"] == "extras")
    assert (step["score"], step["max"], step["points_available"]) == (0, 0, 0)
    assert step["status"] == "optional"
    assert step["findings"] == []
    # The internal `scored` flag must not leak into the contract.
    assert "scored" not in step


def test_filling_in_extras_does_not_change_the_score(client, priya):
    """A user adding certifications must see their score stay put."""
    rid = new_resume(client, priya, "Extras", WEAK)
    before = _steps(client, priya, rid)
    doc = client.get(f"/api/resumes/{rid}",
                     headers={"X-User-Id": priya}).json()["structured_data"]
    doc["extras"] = [{"kind": "certifications", "title": "Certifications",
                      "entries": [{"primary": "AWS Solutions Architect",
                                   "secondary": "AWS", "date": "2023",
                                   "detail": ""}]}]
    assert client.put(f"/api/resumes/{rid}/data", json={"structured_data": doc},
                      headers={"X-User-Id": priya}).status_code == 200
    after = _steps(client, priya, rid)
    assert after["overall_score"] == before["overall_score"]
    assert after["max_score"] == before["max_score"] == 100
    assert after["points_available"] == before["points_available"]


def test_a_legacy_resume_with_no_extras_key_still_returns_five_steps(
    client, priya, priya_resume,
):
    body = _steps(client, priya, priya_resume)
    assert len(body["steps"]) == 5


def test_steps_work_before_any_analysis_has_been_run(client, priya):
    """The guided flow produces the first score, so it cannot require one."""
    rid = new_resume(client, priya, "Never analysed", WEAK)
    # Confirm the precondition rather than assuming it.
    assert client.get(f"/api/resumes/{rid}/analysis",
                      headers={"X-User-Id": priya}).status_code == 404
    body = _steps(client, priya, rid)
    assert body["points_available"] > 0


def test_totals_are_internally_consistent(client, priya):
    rid = new_resume(client, priya, "Weak", WEAK)
    body = _steps(client, priya, rid)
    assert body["overall_score"] == sum(s["score"] for s in body["steps"])
    assert body["points_available"] == sum(
        s["points_available"] for s in body["steps"]
    )
    for s in body["steps"]:
        assert s["points_available"] == sum(f["points"] for f in s["findings"])
        assert s["score"] + s["points_available"] <= s["max"]


def test_every_finding_carries_a_usable_payload(client, priya):
    rid = new_resume(client, priya, "Weak", WEAK)
    body = _steps(client, priya, rid)
    findings = [f for s in body["steps"] for f in s["findings"]]
    assert findings, "a deliberately weak resume produced no findings"
    for f in findings:
        assert f["points"] > 0
        assert f["target_ref"]
        assert f["message"] and f["fix_hint"]
        assert f["severity"] in {"high", "medium", "low"}


def test_fixing_a_field_raises_the_score_by_the_promised_points(client, priya):
    """End-to-end honesty check, through HTTP, the way the UI will see it."""
    rid = new_resume(client, priya, "Weak", WEAK)
    before = _steps(client, priya, rid)
    card = next(
        f for s in before["steps"] for f in s["findings"]
        if f["id"] == "contact.email.invalid"
    )

    data = dict(WEAK)
    data["contact"] = {**WEAK["contact"], "email": "sam@example.com"}
    r = client.put(f"/api/resumes/{rid}/data", json={"structured_data": data},
                   headers={"X-User-Id": priya})
    assert r.status_code == 200, r.text

    after = _steps(client, priya, rid)
    assert after["overall_score"] - before["overall_score"] == card["points"]
    ids = {f["id"] for s in after["steps"] for f in s["findings"]}
    assert "contact.email.invalid" not in ids


def test_steps_reflect_edits_immediately_without_reanalysing(client, priya):
    """Steps are live; a stale cached report would break the whole flow."""
    rid = new_resume(client, priya, "Weak", WEAK)
    client.post(f"/api/resumes/{rid}/analyze", headers={"X-User-Id": priya})
    stale = client.get(f"/api/resumes/{rid}/analysis",
                       headers={"X-User-Id": priya}).json()["overall_score"]

    data = {**WEAK, "skills": [{"label": "Core", "items": ["Python", "AWS"]}]}
    client.put(f"/api/resumes/{rid}/data", json={"structured_data": data},
               headers={"X-User-Id": priya})

    live = _steps(client, priya, rid)["overall_score"]
    frozen = client.get(f"/api/resumes/{rid}/analysis",
                        headers={"X-User-Id": priya}).json()["overall_score"]
    assert live > stale, "steps did not pick up the edit"
    assert frozen == stale, "stored report should stay frozen until re-analyse"


def test_steps_are_user_scoped(client, arjun, priya_resume):
    r = client.get(f"/api/resumes/{priya_resume}/analysis/steps",
                   headers={"X-User-Id": arjun})
    assert r.status_code == 404


def test_steps_are_deterministic(client, priya, priya_resume):
    first = _steps(client, priya, priya_resume)
    second = _steps(client, priya, priya_resume)
    assert first == second


def test_legacy_analysis_payload_still_carries_notes(client, priya, priya_resume):
    """category_scores[c].notes must survive for existing clients."""
    r = client.post(f"/api/resumes/{priya_resume}/analyze",
                    headers={"X-User-Id": priya})
    assert r.status_code == 201
    cats = r.json()["category_scores"]
    for name in ("contact", "summary", "experience", "format"):
        assert "notes" in cats[name]
        assert isinstance(cats[name]["notes"], list)
        assert all(isinstance(n, str) for n in cats[name]["notes"])
