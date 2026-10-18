"""Staleness judged on identity, not only on text.

The documented hole: two roles carrying a byte-identical bullet. Reorder
them and `original_text` still matches the text at the ref, so the
suggestion looks fresh -- while the ref now addresses a different employer.
Accepting applies it to the wrong job, silently.

The anchor is `Suggestion.placement`, which already existed and which
`iter_bullets` fills with "Company — Role".
"""

import uuid

import pytest

from app.models import Suggestion
from app.services import resume_ops
from app.services.suggestion_service import _is_stale


def _data(first="Acme", second="Globex"):
    """Two roles whose FIRST bullet is byte-identical."""
    shared = "Maintained the continuous deployment pipeline"
    return {
        "contact": {},
        "summary": {"text": "Platform engineer."},
        "experience": [
            {"company": first, "role": "Senior SRE", "dates": "2022-2024",
             "bullets": [shared, "Ran the on-call rotation"]},
            {"company": second, "role": "Senior SRE", "dates": "2020-2022",
             "bullets": [shared, "Built the deploy tooling"]},
        ],
        "projects": [],
        "education": [],
        "skills": [],
    }


def _row(target_ref, original_text, placement, status="pending"):
    return Suggestion(
        id=uuid.uuid4(), resume_id=uuid.uuid4(), session_id=None,
        origin="tailor", section="experience", target_ref=target_ref,
        placement=placement, original_text=original_text,
        suggested_text="something better", keywords=[], reasoning="",
        status=status,
    )


# ------------------------------------------------------------ placement_for
def test_placement_for_matches_what_iter_bullets_writes():
    """Two spellings of one label would mark every suggestion stale."""
    data = _data()
    from_iter = {ref: place for ref, place, _ in resume_ops.iter_bullets(data)}
    for ref, placement in from_iter.items():
        assert resume_ops.placement_for(data, ref) == placement, ref


def test_placement_for_summary_and_missing_entries():
    data = _data()
    assert resume_ops.placement_for(data, "summary.text") == "Summary"
    assert resume_ops.placement_for(data, "exp_9.bullet_0") == ""


def test_placement_for_education_and_skills():
    data = _data()
    data["education"] = [{"school": "Pune University", "degree": "B.E."}]
    data["skills"] = [{"label": "Platform", "items": ["Go"]}]
    assert resume_ops.placement_for(data, "edu_0.school") == "Pune University — B.E."
    assert resume_ops.placement_for(data, "skills.0.items") == "Platform"


# ----------------------------------------------------------------- the hole
def test_the_duplicated_bullet_reorder_is_now_caught():
    """THE regression this file exists for.

    Suggestion written against Acme's first bullet. The roles are then
    swapped. The text at exp_0.bullet_0 is still byte-identical, so the
    text check passes -- but exp_0 is Globex now.
    """
    before = _data()
    row = _row("exp_0.bullet_0", before["experience"][0]["bullets"][0], "Acme — Senior SRE")
    assert _is_stale(before, row) is False, "fresh before anything moves"

    after = _data()
    after["experience"].reverse()
    assert resume_ops.resolve(after, "exp_0.bullet_0") == row.original_text, (
        "precondition: the text check cannot see this change"
    )
    assert _is_stale(after, row) is True, (
        "the ref now points at Globex; accepting would edit the wrong job"
    )


def test_an_untouched_resume_is_not_stale():
    """Guard against the fix firing on everything."""
    data = _data()
    row = _row("exp_0.bullet_0", data["experience"][0]["bullets"][0], "Acme — Senior SRE")
    assert _is_stale(data, row) is False


def test_edited_text_is_still_stale_as_before():
    data = _data()
    row = _row("exp_0.bullet_0", "something the resume no longer says", "Acme — Senior SRE")
    assert _is_stale(data, row) is True


def test_a_deleted_section_is_still_stale_as_before():
    data = _data()
    data["experience"] = []
    row = _row("exp_0.bullet_0", "anything", "Acme — Senior SRE")
    assert _is_stale(data, row) is True


# ------------------------------------------- not firing where it must not
def test_a_placement_that_is_not_a_placement_is_ignored():
    """Analysis rows historically stored the finding message in this column.

    An anchor that matches no entry proves nothing. Treating it as "moved"
    would mark every legacy analysis suggestion stale -- a worse bug than
    the one being fixed.
    """
    data = _data()
    row = _row(
        "exp_0.bullet_0",
        data["experience"][0]["bullets"][0],
        "Only 1 of 4 bullets are quantified",   # a message, not a placement
    )
    assert _is_stale(data, row) is False


def test_an_empty_placement_is_ignored():
    data = _data()
    row = _row("exp_0.bullet_0", data["experience"][0]["bullets"][0], "")
    assert _is_stale(data, row) is False


def test_renaming_the_company_does_not_flag_it():
    """The user fixing a typo in their employer's name is not a reorder.

    The stored anchor no longer matches ANY entry, so we cannot show that
    the ref moved -- and must not guess.
    """
    data = _data(first="Acme Corporation")
    row = _row("exp_0.bullet_0", data["experience"][0]["bullets"][0], "Acme — Senior SRE")
    assert _is_stale(data, row) is False


@pytest.mark.parametrize("status", ["accepted", "rejected", "needs_input"])
def test_only_pending_suggestions_can_be_stale(status):
    after = _data()
    after["experience"].reverse()
    row = _row("exp_0.bullet_0", _data()["experience"][0]["bullets"][0],
               "Acme — Senior SRE", status=status)
    assert _is_stale(after, row) is False
