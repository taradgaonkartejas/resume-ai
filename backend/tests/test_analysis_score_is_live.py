"""The Analyze tab's score must never be a frozen copy.

Reported sequence, reproduced below: fix recommendations in the guided editor
and the score climbs 79 -> 93. Click Tailor Resume, switch back to Analyze,
and it reads 79 again -- until "Improve my resume" is pressed, which re-enters
the stepper and shows the live number.

Nothing was wrong with the client cache. `GET /analysis/steps` recomputed the
score from the current structured_data while `GET /analysis` replayed the
AnalysisReport row written by the last POST /analyze, so the two endpoints
answered the same question differently and the UI believed whichever it had
most recently rendered. Invalidating the query just refetched the same stale
number.

The score is a pure function of structured_data with fixed weights, so there
is no reason for a second copy to exist. These tests pin that: the
deterministic half is recomputed on read, the LLM prose stays a snapshot and
says so, and reading never writes.
"""

from __future__ import annotations

import uuid as _uuid

from app.services.heuristics import score_resume
from tests.conftest import new_resume

# Deliberately imperfect: no phone, no location, so there is headroom to
# "fix" and watch the number move.
WEAK = {
    "contact": {"name": "Sam Rivera", "headline": "Engineer", "email": "s@x.io",
                "phone": "", "location": "", "links": []},
    "summary": {"text": "Engineer with six years building platforms."},
    "experience": [{
        "company": "Acme", "role": "Engineer", "dates": "2020 - 2024",
        "bullets": ["Cut build times from 20 minutes to 6 minutes."],
    }],
    "projects": [], "education": [],
    "skills": [{"label": "Languages", "items": ["Python", "SQL"]}],
}


def _analysis(client, user_id, resume_id):
    r = client.get(f"/api/resumes/{resume_id}/analysis",
                   headers={"X-User-Id": user_id})
    assert r.status_code == 200, r.text
    return r.json()


def _steps(client, user_id, resume_id):
    r = client.get(f"/api/resumes/{resume_id}/analysis/steps",
                   headers={"X-User-Id": user_id})
    assert r.status_code == 200, r.text
    return r.json()


def _seed_report(client, resume_id, *, stored_score: int):
    """A stored report whose number is deliberately WRONG for the content."""
    from app.models import AnalysisReport

    db = client._session_factory()
    db.add(AnalysisReport(
        resume_id=_uuid.UUID(str(resume_id)),
        overall_score=stored_score,
        category_scores={"contact": {"score": 0, "max": 15, "notes": [],
                                     "findings": []}},
        role_tags=["backend"],
        trace={"commentary": {"headline": "Written about the OLD text.",
                              "category_notes": {}}},
    ))
    db.commit()
    return db


def test_the_two_endpoints_cannot_disagree_about_the_score(client, priya):
    """The invariant the whole bug violated."""
    resume_id = new_resume(client, priya, "Agreement", dict(WEAK))
    _seed_report(client, resume_id, stored_score=79)

    assert (_analysis(client, priya, resume_id)["overall_score"]
            == _steps(client, priya, resume_id)["overall_score"])


def test_the_stored_number_does_not_win(client, priya):
    """79 was persisted; the content does not score 79 any more."""
    resume_id = new_resume(client, priya, "Stale", dict(WEAK))
    _seed_report(client, resume_id, stored_score=79)

    live = score_resume(dict(WEAK))["overall_score"]
    assert live != 79, "pick a stored score the content cannot produce"
    assert _analysis(client, priya, resume_id)["overall_score"] == live


def test_fixing_the_resume_moves_the_score_without_re_running_analyze(
    client, priya,
):
    """The reported sequence, end to end.

    Edit the resume the way the guided editor does, then read the Analyze
    tab. No POST /analyze in between -- that is the whole complaint.
    """
    from app.models import Resume

    resume_id = new_resume(client, priya, "Improves", dict(WEAK))
    db = _seed_report(client, resume_id, stored_score=79)

    before = _analysis(client, priya, resume_id)["overall_score"]

    resume = db.get(Resume, _uuid.UUID(str(resume_id)))
    data = dict(resume.structured_data or {})
    # A SCORED fix: contact is an advisory gate (0 points) since 2026-10-25,
    # so fixing contact fields deliberately cannot move the number any more.
    data["skills"] = [
        {"label": "Core", "items": ["Python", "Go", "SQL", "AWS"]},
        {"label": "Platform", "items": ["Kubernetes", "Docker", "Linux", "Terraform"]},
    ]
    resume.structured_data = data
    db.commit()

    after = _analysis(client, priya, resume_id)["overall_score"]
    assert after > before, (
        "the Analyze tab still reports the score from the last analysis run"
    )
    assert after == _steps(client, priya, resume_id)["overall_score"]


def test_category_scores_and_findings_are_live_too(client, priya):
    """Not just the headline number.

    The pane renders per-category bars and the "N recommendations" count from
    category_scores, so a live total over stale categories would be a new
    inconsistency rather than a fix.
    """
    from app.models import Resume

    resume_id = new_resume(client, priya, "Categories", dict(WEAK))
    db = _seed_report(client, resume_id, stored_score=79)

    body = _analysis(client, priya, resume_id)
    skills_before = body["category_scores"]["skills"]["score"]
    # The seeded row claimed 0 for a scored category; a live read must not
    # echo it. (Skills, not contact: contact now caps at 0 by design.)
    assert skills_before < 30
    assert skills_before < body["category_scores"]["skills"]["max"]

    resume = db.get(Resume, _uuid.UUID(str(resume_id)))
    data = dict(resume.structured_data or {})
    data["skills"] = [
        {"label": "Core", "items": ["Python", "Go", "SQL", "AWS"]},
        {"label": "Platform", "items": ["Kubernetes", "Docker", "Linux", "Terraform"]},
    ]
    resume.structured_data = data
    db.commit()

    after = _analysis(client, priya, resume_id)["category_scores"]["skills"]
    assert after["score"] > skills_before
    # Fixed findings disappear rather than lingering as unearnable points.
    refs = {f["id"] for f in after["findings"]}
    assert "skills.missing" not in refs


def test_reading_the_analysis_does_not_rewrite_the_stored_report(client, priya):
    """A GET must not turn into a write.

    Overlaying fresh numbers onto the ORM row would mark it dirty and the next
    commit would overwrite the historical record -- so latest() returns a dict
    and the row is left exactly as written.
    """
    from app.models import AnalysisReport

    resume_id = new_resume(client, priya, "Immutable", dict(WEAK))
    db = _seed_report(client, resume_id, stored_score=79)

    _analysis(client, priya, resume_id)

    db.expire_all()
    row = (db.query(AnalysisReport)
             .filter(AnalysisReport.resume_id == _uuid.UUID(str(resume_id)))
             .one())
    assert row.overall_score == 79, "the read mutated the stored report"
    assert row.category_scores["contact"]["score"] == 0


def test_the_prose_is_stamped_rather_than_silently_refreshed(client, priya):
    """Commentary is a snapshot; regenerating it costs a model call.

    So it travels with the same staleness flag the guided editor uses, and
    the two screens must agree about it.
    """
    resume_id = new_resume(client, priya, "Prose", dict(WEAK))
    _seed_report(client, resume_id, stored_score=79)

    body = _analysis(client, priya, resume_id)
    assert body["commentary"]["headline"] == "Written about the OLD text."
    # No fingerprint was recorded, so it cannot prove it is current.
    assert body["commentary_stale"] is True
    assert body["commentary_stale"] == _steps(
        client, priya, resume_id,
    )["commentary_stale"]


def test_never_analysed_still_404s(client, priya, priya_resume):
    """The empty state is unchanged: no report means no report.

    The score is computable without one, but "never analysed" is a real
    state the pane renders differently, and inventing a report here would
    erase it.
    """
    r = client.get(f"/api/resumes/{priya_resume}/analysis",
                   headers={"X-User-Id": priya})
    assert r.status_code == 404


def test_the_library_card_agrees_with_the_analyze_tab(client, priya):
    """Third surface, same number.

    MyResumes renders a strength ring per card from this field. It read the
    stored report, so a card could say 79 while the tab beside it said 93 for
    the same resume.
    """
    resume_id = new_resume(client, priya, "Card", dict(WEAK))
    _seed_report(client, resume_id, stored_score=79)

    rows = client.get("/api/resumes", headers={"X-User-Id": priya}).json()
    row = next(r for r in rows if str(r["id"]) == str(resume_id))

    assert row["overall_score"] == _analysis(
        client, priya, resume_id,
    )["overall_score"]
    assert row["overall_score"] != 79


def test_never_analysed_cards_keep_their_empty_state(client, priya, priya_resume):
    """`None` is a state, not a missing number.

    The score is computable without a report, but the library renders "Run an
    analysis to see your score" for None and inventing a number would erase
    that distinction.
    """
    rows = client.get("/api/resumes", headers={"X-User-Id": priya}).json()
    row = next(r for r in rows if str(r["id"]) == str(priya_resume))
    assert row["overall_score"] is None
