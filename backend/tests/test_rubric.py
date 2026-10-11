"""The scoring rubric and the rule writer must not contradict each other.

The live writer measured 4/7 on the corpus, and all three misses had the same
shape: it fixed the finding it was given and tripped a *different* one. None
of them was bad prose. They were the rules disagreeing with each other:

    summary_too_short           expanded it  -> summary.no_action_verb
    summary_absent              composed one -> summary.too_short
    bullet_overlong_and_padded  trimmed it   -> experience.unquantified_bullets

Two of those are rubric defects that also hit real users directly, with no
model involved at all. This file pins the fixes.
"""

from __future__ import annotations

import pytest

from app.ai import agents
from app.services.heuristics import ACTION_VERBS, contains, score_resume

# --------------------------------------------------------------------------
# ACTION_VERBS: the list was past-tense only
# --------------------------------------------------------------------------

PARTICIPLE_SUMMARIES = [
    "Platform engineer with 6 years building distributed systems.",
    "Staff engineer leading a team of eight across two regions.",
    "Infrastructure specialist running production Kubernetes at scale.",
    "Backend engineer migrating monoliths to event-driven services.",
    "SRE driving reliability work across a large estate.",
]


@pytest.mark.parametrize("text", PARTICIPLE_SUMMARIES)
def test_a_participle_summary_is_credited_with_an_action_verb(text):
    """"6 years BUILDING distributed systems" is not a weak summary.

    The set held `built` and not `building`, so ordinary, well-written
    summaries raised summary.no_action_verb -- a finding whose fix hint says
    "Open with a verb like 'Led', 'Built' or 'Scaled'", which the user has
    already effectively done. They cannot act on it.
    """
    assert any(contains(text, v) for v in ACTION_VERBS), text


@pytest.mark.parametrize("verb", ["cut", "ran", "leading", "building", "managing"])
def test_the_specific_gaps_are_closed(verb):
    """`reduced` and `led` were present while `cut` and `leading` were not."""
    assert verb in ACTION_VERBS


def test_no_original_verb_was_lost_when_the_list_was_split():
    """Splitting into past + participle sets dropped `orchestrated` once.

    Restructuring a literal is exactly where a silent deletion hides, so the
    whole original list is asserted rather than spot-checked.
    """
    original = {
        "accelerated", "analysed", "analyzed", "architected", "authored",
        "automated", "built", "championed", "coached", "collaborated",
        "consolidated", "coordinated", "created", "delivered", "deployed",
        "designed", "developed", "diagnosed", "directed", "drove",
        "eliminated", "engineered", "established", "evaluated", "expanded",
        "fixed", "forecast", "founded", "generated", "grew", "guided",
        "hardened", "headed", "identified", "implemented", "improved",
        "increased", "influenced", "introduced", "investigated", "launched",
        "led", "managed", "measured", "mentored", "migrated", "modeled",
        "modelled", "modernised", "modernized", "negotiated", "optimised",
        "optimized", "orchestrated", "oversaw", "owned", "partnered",
        "presented", "produced", "prototyped", "rearchitected", "recovered",
        "reduced", "refactored", "researched", "resolved", "restructured",
        "saved", "scaled", "secured", "shipped", "simplified", "spearheaded",
        "standardised", "standardized", "streamlined", "transformed",
        "upgraded",
    }
    assert original <= ACTION_VERBS, f"lost: {sorted(original - ACTION_VERBS)}"


# --------------------------------------------------------------------------
# _STRONGER: a swap must be faithful AND effective
# --------------------------------------------------------------------------

#: Verbs that assert ownership or completion. Swapping *into* one of these is
#: only legitimate when the original already asserted it.
_CLAIMS_OWNERSHIP = {
    "led", "drove", "owned", "delivered", "spearheaded", "headed", "directed",
    "founded", "architected",
}


def test_the_strong_swap_map_is_exactly_what_was_agreed():
    """Pinned deliberately. Changing it should be a decision, not a drift."""
    assert agents._STRONGER == {
        "responsible": "owned",
        "handled": "managed",
        "made": "built",
    }


@pytest.mark.parametrize("weak", ["involved", "helped", "worked"])
def test_the_rule_engine_does_not_promote_a_supporting_role(weak):
    """involved -> led, helped -> drove and worked -> delivered are fabrications.

    They claim more than the source. The numeric guard cannot catch them: it
    reads digits, and these invent no digit. So unlike the model's inventions
    -- 58, 59, 99, all caught -- these shipped to the resume, produced
    deterministically by code we own.

    There is no faithful strong verb for "was involved in", so the correct
    output is no suggestion at all.
    """
    assert weak not in agents._STRONGER


def test_every_surviving_swap_is_faithful():
    """`responsible for X` really does mean ownership of X; that one is fine."""
    allowed = {"responsible"}
    for weak, strong in agents._STRONGER.items():
        if strong in _CLAIMS_OWNERSHIP:
            assert weak in allowed, f"{weak} -> {strong} claims more than the original"


def test_every_surviving_swap_actually_clears_the_finding():
    """A swap whose replacement is not an action verb changes the wording and
    improves nothing -- the user accepts a suggestion and the score does not
    move. Four of the original ten did this (`assisted` -> `supported`,
    `did` -> `executed`, `used` -> `applied`,
    `participated` -> `contributed`).
    """
    for weak, strong in agents._STRONGER.items():
        assert strong in ACTION_VERBS, f"{weak} -> {strong} leaves the finding open"


# --------------------------------------------------------------------------
# trimming an overlong bullet must not throw away the metric
# --------------------------------------------------------------------------

# The scorer emits format.overlong_bullets, and the threshold is
# BULLET_HARD_CAP (45 words), not BULLET_COACHED_MAX. _rule_rewrite dispatches
# on fid.endswith("overlong_bullets"), so the prefix does not matter to the
# rewrite -- but it very much matters to a test that asserts the finding fires.
_FINDING = {"id": "format.overlong_bullets", "target_ref": "exp_0.bullet_0"}


def _trim(text: str) -> str:
    return (
        agents._rule_rewrite(text, _FINDING, False, {}).get("suggested_text") or ""
    ).strip()


def test_trimming_keeps_a_clause_that_carries_the_number():
    """The padding that made the bullet overlong was carrying the metric.

    Taking parts[0] fixed experience.overlong_bullets and raised
    experience.unquantified_bullets in the same edit.
    """
    text = (
        "Managed the migration of the legacy billing platform to AWS, "
        "which cut processing time by 40%, working across many teams "
        "and several regions over the course of the year."
    )
    out = _trim(text)
    assert out, "should have produced a trim"
    assert "40%" in out, f"dropped the metric: {out}"
    assert len(out.split()) < len(text.split())


def test_trimming_a_bullet_with_no_number_is_unaffected():
    text = (
        "Managed the migration of the legacy billing platform to AWS, "
        "working across many teams and several regions over the year."
    )
    out = _trim(text)
    assert out and len(out.split()) < len(text.split())


def test_trimming_declines_rather_than_dropping_the_only_metric():
    """If no prefix is both short enough and still quantified, say nothing.

    Declining is the documented correct answer; the corpus scores an honest
    decline above a cosmetic edit.
    """
    text = (
        "Managed a very large and complicated multi-year programme of work "
        "across the whole organisation with many stakeholders involved, "
        "and it reduced cost by 30%."
    )
    out = _trim(text)
    assert out == "" or "30%" in out, f"trim silently dropped the metric: {out}"


def test_a_trimmed_bullet_does_not_introduce_a_new_finding():
    """The property the corpus actually measures: fix one, break none."""
    data = {
        "contact": {"name": "T", "email": "t@e.com", "phone": "+1"},
        "summary": {"text": ""},
        "experience": [
            {
                "company": "Acme",
                "title": "Engineer",
                "start_date": "2020",
                "end_date": "2024",
                # Must exceed BULLET_HARD_CAP (45 words) or the overlong rule
                # never fires. Two earlier drafts of this fixture (26 and 40
                # words) did not, and the test passed by skipping.
                "bullets": [
                    "Managed the migration of the legacy billing platform to "
                    "AWS, which cut processing time by 40%, working across "
                    "many different teams and several geographic regions "
                    "over the course of the entire financial year alongside "
                    "a large number of external stakeholders and partners, "
                    "coordinating schedules and reviewing designs with every "
                    "group involved until the final cutover was completed.",
                ],
            }
        ],
        "education": [],
        "skills": [{"label": "Cloud", "items": ["AWS", "Terraform"]}],
        "projects": [],
        "certifications": [],
    }
    before = score_resume(data)
    ids_before = {f["id"] for f in before["findings"]}
    assert "format.overlong_bullets" in ids_before, (
        "the fixture must actually be overlong, or this test asserts nothing"
    )

    from app.services import resume_ops

    out = _trim(data["experience"][0]["bullets"][0])
    assert out, "a 50-word bullet with an early metric should be trimmable"
    after = score_resume(resume_ops.apply_patch(data, "exp_0.bullet_0", out))
    introduced = {f["id"] for f in after["findings"]} - ids_before
    assert not introduced, f"trim introduced {sorted(introduced)}"
