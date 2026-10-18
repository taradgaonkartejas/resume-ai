"""Reordering experience: the fix for a finding that had none.

`ats.not_reverse_chronological` has always been detectable and scored, and
until now carried `action=""` -- "Go to field". It told the user to order
their roles newest-first and gave them no control that could do it.

The operation is deterministic, so unlike a rewrite there is no model, no
token cost and nothing to fabricate -- provided the result really is a
permutation of the input, which is asserted here and in the service.
"""

from __future__ import annotations

import uuid as _uuid

import pytest

from app.models import Suggestion
from app.services import ats, resume_ops
from app.services.heuristics import score_resume
from tests.conftest import new_resume

# Deliberately out of order: 2019 sits above 2023.
SAMPLE = {
    "contact": {"name": "Sam Rivera", "headline": "Engineer", "email": "s@x.io",
                "phone": "+1 555 0100", "location": "Pune", "links": []},
    "summary": {"text": "Platform engineer with six years building and running "
                        "cloud infrastructure for high-traffic services."},
    "experience": [
        {"company": "Oldcorp", "role": "Junior", "start_date": "2017",
         "end_date": "2019", "bullets": ["Shipped the billing service."]},
        {"company": "Newcorp", "role": "Senior", "start_date": "2021",
         "end_date": "2023", "bullets": ["Cut build times to 6 minutes."]},
    ],
    "projects": [], "education": [],
    "skills": [{"label": "Languages", "items": ["Python", "SQL"]}],
}


def _roles(data: dict) -> list[str]:
    return [e["role"] for e in data["experience"]]


# --------------------------------------------------------------- pure ops ---
def test_sorts_newest_first():
    out = resume_ops.reorder_experience(SAMPLE)
    assert _roles(out) == ["Senior", "Junior"]


def test_does_not_mutate_its_input():
    before = _roles(SAMPLE)
    resume_ops.reorder_experience(SAMPLE)
    assert _roles(SAMPLE) == before


def test_clears_the_finding_that_motivates_it():
    """The whole point: the check that grades ordering must agree."""
    assert ats.out_of_order_experience(SAMPLE) is True
    assert ats.out_of_order_experience(resume_ops.reorder_experience(SAMPLE)) is False


def test_undated_entries_sort_last_and_keep_their_order():
    data = {"experience": [
        {"role": "NoDateA"}, {"role": "Old", "end_date": "2019"},
        {"role": "NoDateB"}, {"role": "New", "end_date": "2023"},
    ]}
    assert _roles(resume_ops.reorder_experience(data)) == [
        "New", "Old", "NoDateA", "NoDateB",
    ]


def test_already_ordered_is_a_no_op():
    ordered = resume_ops.reorder_experience(SAMPLE)
    assert resume_ops.reorder_experience(ordered) == ordered


def test_empty_and_single_entry_are_safe():
    assert resume_ops.reorder_experience({})["experience"] == []
    one = {"experience": [{"role": "Solo"}]}
    assert _roles(resume_ops.reorder_experience(one)) == ["Solo"]


def test_permutation_guard_rejects_content_change():
    """Guard the guard.

    This is the entire safety argument for rerank -- that it can only
    permute. If the assertion can't fail, the argument is worthless.
    """
    with pytest.raises(RuntimeError, match="not just order"):
        resume_ops._assert_permutation([{"a": 1}], [{"a": 2}])
    with pytest.raises(RuntimeError, match="not just order"):
        resume_ops._assert_permutation([{"a": 1}, {"b": 2}], [{"a": 1}])
    # A genuine reordering must pass it.
    resume_ops._assert_permutation([{"a": 1}, {"b": 2}], [{"b": 2}, {"a": 1}])


def test_the_finding_is_now_actionable():
    findings = [
        f for f in score_resume(SAMPLE)["findings"]
        if f["id"] == "ats.not_reverse_chronological"
    ]
    assert findings, "fixture should be out of order"
    assert findings[0]["action"] == "rerank"


# ------------------------------------------------------------------- API ---
def test_rerank_endpoint_reorders_and_pays_out(client, priya):
    """End to end: the finding disappears and the score rises by its points."""
    resume_id = new_resume(client, priya, "Chrono", dict(SAMPLE))

    before = score_resume(SAMPLE)
    promised = next(
        f["points"] for f in before["findings"]
        if f["id"] == "ats.not_reverse_chronological"
    )

    r = client.post(
        f"/api/resumes/{resume_id}/sections/experience/rerank",
        headers={"X-User-Id": str(priya)},
    )
    assert r.status_code == 200, r.text
    data = r.json()["structured_data"]
    assert _roles(data) == ["Senior", "Junior"]

    after = score_resume(data)
    assert not [
        f for f in after["findings"] if f["id"] == "ats.not_reverse_chronological"
    ]
    assert after["overall_score"] - before["overall_score"] == promised


def test_rerank_records_a_version(client, priya):
    resume_id = new_resume(client, priya, "Chrono", dict(SAMPLE))
    h = {"X-User-Id": str(priya)}
    before = len(client.get(f"/api/resumes/{resume_id}/versions", headers=h).json())
    client.post(f"/api/resumes/{resume_id}/sections/experience/rerank", headers=h)
    after = len(client.get(f"/api/resumes/{resume_id}/versions", headers=h).json())
    assert after == before + 1


def test_rerank_of_ordered_resume_adds_no_version(client, priya):
    """A no-op must not litter the history with identical snapshots."""
    ordered = resume_ops.reorder_experience(SAMPLE)
    resume_id = new_resume(client, priya, "Tidy", ordered)
    h = {"X-User-Id": str(priya)}
    before = len(client.get(f"/api/resumes/{resume_id}/versions", headers=h).json())
    r = client.post(
        f"/api/resumes/{resume_id}/sections/experience/rerank", headers=h
    )
    assert r.status_code == 200
    after = len(client.get(f"/api/resumes/{resume_id}/versions", headers=h).json())
    assert after == before


def test_rerank_refuses_while_a_suggestion_is_open(client, priya):
    """Positional refs: reordering would re-point this draft at another role."""
    resume_id = new_resume(client, priya, "Chrono", dict(SAMPLE))
    db = client._session_factory()
    db.add(Suggestion(
        id=_uuid.uuid4(), resume_id=_uuid.UUID(resume_id),
        target_ref="exp_0.bullet_0", original_text="Shipped the billing service.",
        suggested_text="Shipped billing, cutting disputes 30%.",
        status="pending", origin="analysis",
    ))
    db.commit()
    db.close()

    r = client.post(
        f"/api/resumes/{resume_id}/sections/experience/rerank",
        headers={"X-User-Id": str(priya)},
    )
    assert r.status_code == 409
    assert "experience" in r.json()["detail"]

    # and the order is genuinely untouched
    got = client.get(
        f"/api/resumes/{resume_id}", headers={"X-User-Id": str(priya)}
    ).json()["structured_data"]
    assert _roles(got) == ["Junior", "Senior"]


def test_a_resolved_suggestion_does_not_block(client, priya):
    """Only *pending* drafts are at risk -- a rejected one is nobody's problem."""
    resume_id = new_resume(client, priya, "Chrono", dict(SAMPLE))
    db = client._session_factory()
    db.add(Suggestion(
        id=_uuid.uuid4(), resume_id=_uuid.UUID(resume_id),
        target_ref="exp_0.bullet_0", original_text="Shipped the billing service.",
        suggested_text="x", status="rejected", origin="analysis",
    ))
    db.commit()
    db.close()

    r = client.post(
        f"/api/resumes/{resume_id}/sections/experience/rerank",
        headers={"X-User-Id": str(priya)},
    )
    assert r.status_code == 200


def test_unreorderable_section_is_refused(client, priya):
    resume_id = new_resume(client, priya, "Chrono", dict(SAMPLE))
    r = client.post(
        f"/api/resumes/{resume_id}/sections/summary.text/rerank",
        headers={"X-User-Id": str(priya)},
    )
    # 422, not 400: InvalidTargetRef is a ValidationError, which is the same
    # code /sections/{ref}/rewrite already returns for an unrecognised ref.
    assert r.status_code == 422
    assert "cannot be reordered" in r.json()["detail"]


def test_rerank_cannot_touch_another_users_resume(client, priya, arjun):
    resume_id = new_resume(client, priya, "Chrono", dict(SAMPLE))
    r = client.post(
        f"/api/resumes/{resume_id}/sections/experience/rerank",
        headers={"X-User-Id": str(arjun)},
    )
    assert r.status_code == 404
