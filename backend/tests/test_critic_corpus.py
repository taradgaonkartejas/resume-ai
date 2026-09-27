"""Measure the critic against the labelled corpus.

Offline (no API key) this pins the RULE ENGINE's capability envelope exactly.
With a key it also measures the LLM critic, which is the only configuration
that can answer "did lowering reasoning_effort make it more permissive?".
"""

import pytest

from app.ai import llm
from app.ai.agents import _rule_critique, review_draft
from tests.critic_cases import CASES, by_kind, fabrications, faithful


def _rule_verdict(case: dict) -> bool:
    """True when the rule engine approves."""
    return _rule_critique(case["original"], {"suggested_text": case["suggested"]}).approved


# ------------------------------------------------ what the rule engine can do
@pytest.mark.parametrize("case", by_kind("numeric"), ids=lambda c: c["id"])
def test_rule_engine_catches_every_numeric_fabrication(case):
    """Set arithmetic over digits -- no model needed, and it works."""
    assert _rule_verdict(case) is False, case["why"]


@pytest.mark.parametrize("case", faithful(), ids=lambda c: c["id"])
def test_rule_engine_does_not_reject_faithful_rewrites(case):
    """False rejections make the product feel broken."""
    assert _rule_verdict(case) is True, case["why"]


@pytest.mark.parametrize("case", by_kind("semantic"), ids=lambda c: c["id"])
def test_rule_engine_is_blind_to_semantic_fabrication(case):
    """Documented weakness, pinned deliberately.

    These all SHOULD be rejected and the rule engine approves every one. This
    is not an accepted behaviour, it is a measurement: it is the exact gap the
    LLM critic exists to close, and the exact damage done when the critic
    silently degrades. If someone teaches _rule_critique to catch one of
    these, this test fails and the corpus gets updated on purpose.
    """
    assert _rule_verdict(case) is True, (
        f"{case['id']} is now caught by the rule engine -- good. "
        "Update rule_engine to 'catches' in tests/critic_cases.py."
    )


def test_the_corpus_matches_its_own_rule_engine_column():
    """critic_cases.py documents what the rule engine does; verify it."""
    for case in CASES:
        approved = _rule_verdict(case)
        caught = approved is False if case["expect"] == "reject" else approved is True
        expected = case["rule_engine"] == "catches"
        assert caught is expected, (
            f"{case['id']}: rule_engine column says {case['rule_engine']!r} "
            f"but the engine {'handled' if caught else 'missed'} it"
        )


def test_the_corpus_is_balanced():
    """A corpus of only fabrications measures nothing useful."""
    assert len(faithful()) >= 4
    assert len(by_kind("numeric")) >= 4
    assert len(by_kind("semantic")) >= 6


def test_rule_engine_scores_exactly_as_expected():
    """The headline number, so a regression is one line in the diff."""
    caught = sum(1 for c in fabrications() if _rule_verdict(c) is False)
    false_rejects = sum(1 for c in faithful() if _rule_verdict(c) is False)
    assert caught == len(by_kind("numeric")), (
        f"rule engine caught {caught}/{len(fabrications())} fabrications; "
        f"expected exactly the {len(by_kind('numeric'))} numeric ones"
    )
    assert false_rejects == 0


# ------------------------------------------------------- the live LLM critic
_HAS_KEY = llm.is_configured()
needs_key = pytest.mark.skipif(_HAS_KEY is False, reason="no UNOROUTER_API_KEY configured")


def _llm_verdict(case: dict) -> bool:
    data = {
        "experience": [
            {"company": "Acme", "role": "Eng", "dates": "", "bullets": [case["original"]]}
        ]
    }
    outcome = review_draft(
        {
            "target_ref": "exp_0.bullet_0",
            "original_text": case["original"],
            "suggested_text": case["suggested"],
            "keywords": [],
        },
        data,
    )
    if outcome.status == "degraded":
        pytest.fail(f"critic degraded instead of judging: {outcome.notes}")
    return outcome.value.approved


@needs_key
@pytest.mark.parametrize("case", by_kind("semantic"), ids=lambda c: c["id"])
def test_llm_critic_closes_the_semantic_gap(case):
    """The whole justification for having an LLM critic at all."""
    assert _llm_verdict(case) is False, case["why"]


@needs_key
@pytest.mark.parametrize("case", faithful(), ids=lambda c: c["id"])
def test_llm_critic_does_not_reject_faithful_rewrites(case):
    assert _llm_verdict(case) is True, case["why"]


@needs_key
def test_llm_critic_recall_beats_the_rule_engine():
    """Minimal reasoning effort must not drop it to rule-engine level."""
    llm_caught = sum(1 for c in fabrications() if _llm_verdict(c) is False)
    rule_caught = sum(1 for c in fabrications() if _rule_verdict(c) is False)
    assert llm_caught > rule_caught, (
        f"LLM critic caught {llm_caught}/{len(fabrications())}, rule engine "
        f"caught {rule_caught}. The LLM is adding nothing -- check "
        f"TASK_EFFORT['critique'] and the model's structured-output support."
    )
