"""The ATS corpus: integrity, the gate's envelope, and the guard.

Split like test_writer_corpus.py -- everything here runs offline except the
live block at the bottom, which skips without `--live`.

The most useful test in this file is not the live one. `mislabelled_sections`
decides which sections the agent ever sees, so **the gate's recall is a hard
ceiling on the agent's recall**: a positive the gate filters out is a case the
model cannot possibly get right, and no amount of prompt work will fix it.
That is measurable offline, for free, and should be checked before anyone
spends a token.
"""

from __future__ import annotations

import pytest

from app.ai import agents, llm
from app.ai.schemas import AtsReview, SectionRelabel, SkillExpansion
from app.services import ats
from tests.ats_cases import (
    CASES,
    DEV,
    EXPANSIONS,
    HOLDOUT,
    expansion_resume,
    negatives,
    positives,
    resume_for,
)

_STANDARD = {
    "Work Experience", "Education", "Projects",
    "Certifications", "Skills", "Volunteer Experience",
}


# --------------------------------------------------------- corpus integrity
def test_case_ids_are_unique():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))


def test_dev_and_holdout_partition_the_corpus():
    """Disjoint and complete, or the holdout is not a holdout."""
    dev, hold = {c["id"] for c in DEV}, {c["id"] for c in HOLDOUT}
    assert dev & hold == set()
    assert dev | hold == {c["id"] for c in CASES}


def test_the_holdout_cannot_be_passed_by_silence():
    """A holdout of only negatives would score 100% by saying nothing."""
    assert positives(HOLDOUT), "holdout has no positive cases"
    assert negatives(HOLDOUT), "holdout has no negative cases"


def test_the_holdout_negatives_actually_reach_the_model():
    """Otherwise the holdout measures the gate, not the judgement.

    The first split failed this: it handed both gate-filtered negatives to
    the holdout, leaving a two-case sample to carry the kill criterion.
    """
    reaching = [
        c["id"] for c in negatives(HOLDOUT)
        if ats.mislabelled_sections(resume_for(c))
    ]
    assert len(reaching) >= 4, f"holdout exam too easy: only {reaching}"


def test_the_known_failure_is_not_in_the_holdout():
    """You cannot iterate against a case and also measure on it."""
    assert "leadership_achievements" in {c["id"] for c in DEV}


def test_negatives_outnumber_positives():
    """The expensive mistake is the false positive, so weight the corpus."""
    assert len(negatives()) >= len(positives())


@pytest.mark.parametrize("case", positives(), ids=lambda c: c["id"])
def test_every_positive_names_a_standard_target(case):
    assert case["expect_target"] in _STANDARD


@pytest.mark.parametrize("case", negatives(), ids=lambda c: c["id"])
def test_no_negative_carries_a_target(case):
    assert "expect_target" not in case


# ------------------------------------------------------- the gate's ceiling
@pytest.mark.parametrize("case", positives(), ids=lambda c: c["id"])
def test_the_gate_lets_every_positive_through(case):
    """A positive the gate drops is an unwinnable case.

    This is the ceiling on recall and it is entirely deterministic, so it is
    worth failing the build over rather than discovering it in a live report
    as a mysteriously missed case.
    """
    found = ats.mislabelled_sections(resume_for(case))
    assert found, f"{case['id']}: gate filtered it out -- the agent never sees it"


def test_the_gate_already_filters_some_negatives_for_free():
    """Not every negative needs a model, and the cheap ones should not get one.

    Sections with no dates never reach the agent at all. Recording which ones
    those are keeps the live score honest: a negative the gate handles is not
    evidence that the *model* has good judgement.
    """
    filtered = [
        c["id"] for c in negatives()
        if not ats.mislabelled_sections(resume_for(c))
    ]
    reaching = [
        c["id"] for c in negatives()
        if ats.mislabelled_sections(resume_for(c))
    ]
    assert filtered, "expected the dateless negatives to be filtered deterministically"
    assert len(reaching) >= 5, (
        "too few negatives actually reach the model -- the exam is too easy: "
        f"{reaching}"
    )


# ---------------------------------------------------------- expansion facts
def test_decoy_acronyms_never_reach_the_model():
    """SQL and REST are the real names of those things.

    They are excluded deterministically by `_NOT_ACRONYMS`, so a model is
    never given the chance to "expand" them. Pinned because deleting an entry
    from that set would silently reintroduce bad advice.
    """
    decoys = [e["acronym"] for e in EXPANSIONS if e.get("decoy")]
    found = ats.bare_acronyms(expansion_resume(decoys))
    assert found == [], f"decoys reached the agent: {found}"


def test_real_acronyms_do_reach_the_model():
    real = [e["acronym"] for e in EXPANSIONS if not e.get("decoy")]
    assert set(ats.bare_acronyms(expansion_resume(real))) == set(real)


def test_the_guard_cannot_catch_a_wrong_expansion():
    """Documents the hole the corpus exists to cover.

    A factually wrong expansion passes every programmatic check: the acronym
    is on the resume and the text is longer. Only pinned expected values
    catch it, which is why EXPANSIONS carries `accept` lists.
    """
    out = agents._clean_ats_review(
        AtsReview(expansions=[
            SkillExpansion(acronym="PMP", expanded="Project Management Process")
        ]),
        [], ["PMP"],
    )
    assert out["expansions"], "guard dropped it for the wrong reason"
    assert "Process" in out["expansions"][0]["expanded"]


# ------------------------------------------------------------------- guards
@pytest.mark.parametrize("case", positives(), ids=lambda c: c["id"])
def test_the_guard_passes_a_correct_relabel(case):
    """The guard must not be the reason a right answer is lost."""
    sections = ats.mislabelled_sections(resume_for(case))
    out = agents._clean_ats_review(
        AtsReview(relabels=[SectionRelabel(
            ref=sections[0]["ref"],
            suggested_title=case["expect_target"],
            reason="because",
        )]),
        sections, [],
    )
    assert len(out["relabels"]) == 1
    assert out["relabels"][0]["suggested_title"] == case["expect_target"]


# --------------------------------------------------------------------- live
@pytest.mark.live
@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_live_agent_judgement(case):
    """One case per test so a failure names the case.

    Thresholds are NOT asserted here -- `scripts/ats_report.py` owns the
    aggregate and the pass/fail decision, because a per-case assert would
    turn one unlucky judgement into a red build. This exists so you can run a
    single case while iterating on the prompt.
    """
    if not llm.is_configured():
        pytest.skip("no provider key")
    out = agents.review_ats(resume_for(case))
    fired = bool(out.value["relabels"])
    if case["expect"] == "leave":
        assert not fired, f"false positive on {case['id']}"
    else:
        assert fired, f"missed {case['id']}"
        assert out.value["relabels"][0]["suggested_title"] == case["expect_target"]
