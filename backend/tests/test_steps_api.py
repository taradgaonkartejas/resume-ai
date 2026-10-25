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


def test_steps_returns_seven_ordered_steps(client, priya, priya_resume):
    """2026-10-25 table: skills/projects became scored steps; contact/format
    became advisory gates that add 0 to the denominator."""
    body = _steps(client, priya, priya_resume)
    assert [s["id"] for s in body["steps"]] == [
        "contact", "summary", "skills", "experience", "projects",
        "format", "extras",
    ]
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


def test_a_legacy_resume_with_no_extras_key_still_returns_seven_steps(
    client, priya, priya_resume,
):
    body = _steps(client, priya, priya_resume)
    assert len(body["steps"]) == 7


def test_projects_step_is_scored_at_fifteen(client, priya, priya_resume):
    """2026-10-25 ruling, reversing "navigable but unscored": projects is a
    15-point scored category (context & frequency)."""
    step = next(s for s in _steps(client, priya, priya_resume)["steps"]
                if s["id"] == "projects")
    assert step["max"] == 15
    assert step["advisory"] is False
    assert "scored" not in step


def test_filling_in_projects_moves_the_score(client, priya):
    """Adding a real projects section to a resume with none earns points."""
    rid = new_resume(client, priya, "Projects", WEAK)
    before = _steps(client, priya, rid)
    assert "projects.missing" in {
        f["id"] for s in before["steps"] for f in s["findings"]
    }
    doc = client.get(f"/api/resumes/{rid}",
                     headers={"X-User-Id": priya}).json()["structured_data"]
    doc["projects"] = [
        {"name": "Portfolio", "bullets": [
            "Built a static-site generator serving 3,000 pages on a Pi",
            "Shipped a docs theme downloaded by 400 users in a year",
        ]},
    ]
    assert client.put(f"/api/resumes/{rid}/data", json={"structured_data": doc},
                      headers={"X-User-Id": priya}).status_code == 200
    after = _steps(client, priya, rid)
    assert after["overall_score"] > before["overall_score"]
    assert after["max_score"] == before["max_score"] == 100


def test_contact_and_format_steps_are_advisory_gates(client, priya):
    """The knockout filter and parser blocker: findings, zero points."""
    rid = new_resume(client, priya, "Weak", WEAK)
    body = _steps(client, priya, rid)
    for step_id in ("contact", "format"):
        step = next(s for s in body["steps"] if s["id"] == step_id)
        assert step["advisory"] is True
        assert (step["score"], step["max"], step["points_available"]) == (0, 0, 0)
        assert all(f["points"] == 0 for f in step["findings"])


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
    # Advisory gates (contact/format) legitimately carry 0; a scored finding
    # never does.
    assert any(f["points"] > 0 for f in findings), "no SCORED finding at all"
    scored = [f for f in findings if f["points"] > 0]
    assert scored
    for f in findings:
        assert f["points"] > 0 or f["category"] in {"contact", "format"}
        assert f["target_ref"]
        assert f["message"] and f["fix_hint"]
        assert f["severity"] in {"high", "medium", "low"}


def test_fixing_a_field_raises_the_score_by_the_promised_points(client, priya):
    """End-to-end honesty check, through HTTP, the way the UI will see it.

    Uses a SCORED card: contact/format cards now promise 0 points by design
    (the advisory gates), so a delta check against them would prove nothing.
    """
    rid = new_resume(client, priya, "Weak", WEAK)
    before = _steps(client, priya, rid)
    card = next(
        f for s in before["steps"] for f in s["findings"]
        if f["id"] == "skills.missing"
    )

    data = dict(WEAK)
    data["skills"] = [
        {"label": "Core", "items": ["Python", "Go", "SQL", "AWS"]},
        {"label": "Platform", "items": ["Kubernetes", "Docker", "Linux", "Terraform"]},
    ]
    r = client.put(f"/api/resumes/{rid}/data", json={"structured_data": data},
                   headers={"X-User-Id": priya})
    assert r.status_code == 200, r.text

    after = _steps(client, priya, rid)
    assert after["overall_score"] - before["overall_score"] == card["points"]
    ids = {f["id"] for s in after["steps"] for f in s["findings"]}
    assert "skills.missing" not in ids


def test_steps_reflect_edits_immediately_without_reanalysing(client, priya):
    """Steps are live; a stale cached report would break the whole flow.

    REVERSED, deliberately. This test used to end with

        assert frozen == stale, "stored report should stay frozen until re-analyse"

    asserting that GET /analysis keeps replaying the last run. That was the
    design, and it was wrong: it made the Analyze tab contradict the guided
    editor on the same resume in the same sitting -- fix recommendations, see
    79 -> 93 in the stepper, switch tabs and back, and the pane says 79 again.
    Reported from the running app.

    The score is a pure function of structured_data, so the two endpoints had
    no business holding separate answers. GET /analysis now recomputes the
    deterministic half, and the assertion below pins the agreement instead.

    The original claim is not discarded, it is moved to the layer where it is
    still true: the stored ROW stays frozen, because a read must not rewrite
    history. `tests/test_analysis_score_is_live.py` pins that.
    """
    rid = new_resume(client, priya, "Weak", WEAK)
    client.post(f"/api/resumes/{rid}/analyze", headers={"X-User-Id": priya})
    stale = client.get(f"/api/resumes/{rid}/analysis",
                       headers={"X-User-Id": priya}).json()["overall_score"]

    data = {**WEAK, "skills": [{"label": "Core", "items": ["Python", "AWS"]}]}
    client.put(f"/api/resumes/{rid}/data", json={"structured_data": data},
               headers={"X-User-Id": priya})

    live = _steps(client, priya, rid)["overall_score"]
    reported = client.get(f"/api/resumes/{rid}/analysis",
                          headers={"X-User-Id": priya}).json()["overall_score"]
    assert live > stale, "steps did not pick up the edit"
    assert reported == live, (
        "the Analyze tab and the guided editor disagree about the same resume"
    )


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
