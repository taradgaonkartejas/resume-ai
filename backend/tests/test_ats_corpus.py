"""The ATS corpus: acronym expansion facts and the guard around them.

This file used to be twice this size. The other half measured section
relabelling, which was deleted on 2026-10-11 after the corpus it ran against
returned a 17% false-positive rate against a pre-registered kill criterion of
10%. The deleted cases, the numbers, and the reasoning are recorded in
`tests/ats_cases.py` and `agents._rule_ats_review`.

What is left is the half that measured FACT rather than judgement. Expansion
is checkable against the resume and against the world: "PMP = Project
Management Process" is simply wrong, and no amount of structural validation
catches it, which is why `EXPANSIONS` pins expected values.

Everything here runs offline except the live block at the bottom, which skips
without `--live`.
"""

from __future__ import annotations

import pytest

from app.ai import agents, llm
from app.ai.schemas import AtsReview, SkillExpansion
from app.services import ats
from tests.ats_cases import EXPANSIONS, expansion_resume


# --------------------------------------------------------- corpus integrity
def test_acronyms_are_unique():
    tokens = [e["acronym"] for e in EXPANSIONS]
    assert len(tokens) == len(set(tokens)), "duplicate acronym in EXPANSIONS"


def test_every_real_entry_pins_an_expected_value():
    """A case with no `accept` list cannot fail, which makes it decoration."""
    for entry in EXPANSIONS:
        if entry.get("decoy"):
            assert entry["accept"] == [], f"{entry['acronym']} is a decoy with accepts"
        else:
            assert entry["accept"], f"{entry['acronym']} pins nothing"


def test_the_corpus_carries_decoys():
    """Without decoys the corpus only rewards firing, never restraint."""
    assert sum(1 for e in EXPANSIONS if e.get("decoy")) >= 2


# ------------------------------------------------------------------ the gate
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
    """The gate's recall is a hard ceiling on the agent's recall.

    A real acronym the gate filters out is a case the model cannot possibly
    get right, and no amount of prompt work will fix it. Measurable offline,
    for free, before anyone spends a token.
    """
    real = [e["acronym"] for e in EXPANSIONS if not e.get("decoy")]
    assert set(ats.bare_acronyms(expansion_resume(real))) == set(real)


# ------------------------------------------------------------------- guards
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
        ["PMP"],
    )
    assert out["expansions"], "guard dropped it for the wrong reason"
    assert "Process" in out["expansions"][0]["expanded"]


@pytest.mark.parametrize("entry", [e for e in EXPANSIONS if not e.get("decoy")],
                         ids=lambda e: e["acronym"])
def test_the_guard_passes_a_correct_expansion(entry):
    """The guard must not be the reason a right answer is lost."""
    out = agents._clean_ats_review(
        AtsReview(expansions=[
            SkillExpansion(acronym=entry["acronym"], expanded=entry["accept"][0])
        ]),
        [entry["acronym"]],
    )
    assert len(out["expansions"]) == 1
    assert entry["accept"][0] in out["expansions"][0]["expanded"].lower()


# --------------------------------------------------------------------- live
@pytest.mark.live
@pytest.mark.parametrize("entry", EXPANSIONS, ids=lambda e: e["acronym"])
def test_live_agent_expansion(entry):
    """One acronym per test so a failure names the acronym.

    Thresholds are NOT asserted here -- `scripts/ats_report.py` owns the
    aggregate and the pass/fail decision, because a per-case assert would
    turn one unlucky judgement into a red build. This exists so you can run a
    single case while iterating on the prompt.
    """
    if not llm.is_configured():
        pytest.skip("no provider key")
    out = agents.review_ats(expansion_resume([entry["acronym"]]))
    got = {e["acronym"]: e["expanded"].lower() for e in out.value["expansions"]}
    if entry.get("decoy"):
        assert entry["acronym"] not in got, f"expanded a decoy: {entry['acronym']}"
    elif entry["acronym"] in got:
        # Silence is acceptable; a WRONG answer is not.
        assert any(a in got[entry["acronym"]] for a in entry["accept"]), (
            f"wrong expansion for {entry['acronym']}: {got[entry['acronym']]!r}"
        )
