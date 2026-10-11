"""The writer-selection gate in scripts/check_model_access.py.

This tool chose a content-safety classifier as the writer because it ranked
on latency alone and the classifier answered in 0.5 seconds. These tests
exist so that cannot recur silently: a model is only offered as a writer if
it produced something a writer would produce.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_model_access import (  # noqa: E402
    _GEN_FACTS,
    _NOT_A_WRITER,
    _writer_blockers,
    _writer_quality,
)

GOOD = (
    "Platform engineer who reduced CI pipeline runtime from 22 minutes to "
    "9 minutes while leading 8 engineers on AWS workloads."
)


# ------------------------------------------------------------- disqualifying
@pytest.mark.parametrize(
    "bare",
    [
        "nvidia/nemotron-3.5-content-safety",
        "meta/llama-3.2-11b-vision-instruct",
        "meta/llama-guard-4-12b",
        "nvidia/nemotron-parse-2.0",
        "some/text-embedding-3",
        "some/bge-reranker",
    ],
)
def test_non_writing_families_are_never_offered(bare):
    assert _NOT_A_WRITER.search(bare), f"{bare} should be excluded by name"


def test_the_classifier_that_was_actually_chosen_is_blocked():
    """Its real reply, verbatim, from the 2026-10-03 run."""
    assert _writer_blockers("nvidia/nemotron-3.5-content-safety", "User Safety: safe")


def test_a_terse_reply_is_blocked_even_from_a_chat_model():
    assert "words" in _writer_blockers("vendor/chat-model", "Reduced 22 to 9.")


def test_dropping_the_supplied_numbers_is_blocked():
    blocked = _writer_blockers("vendor/chat-model", GOOD.replace("22", "twenty-two"))
    assert "dropped supplied number" in blocked


def test_fabricating_a_number_is_blocked_by_the_production_guard():
    """Fabrication outranks every other property, so it disqualifies."""
    text = GOOD.replace("on AWS workloads", "for a 59% improvement")
    blocked = _writer_blockers("vendor/chat-model", text)
    assert "FABRICATED" in blocked and "59" in blocked


def test_a_good_rewrite_is_not_blocked():
    assert _writer_blockers("vendor/chat-model", GOOD) is None


def test_the_facts_string_is_what_the_prompt_actually_supplies():
    """If these drift apart the guard reports fabrications that are not."""
    for n in ("22", "9", "8"):
        assert n in _GEN_FACTS


# -------------------------------------------------------------------- quality
def test_a_clean_rewrite_has_no_defects():
    assert _writer_quality(GOOD) == []


def test_first_person_is_a_defect():
    assert "first person" in _writer_quality(
        "I reduced CI pipeline runtime from 22 minutes to 9 minutes with 8 engineers."
    )


def test_preamble_is_a_defect():
    assert any(
        "preamble" in d
        for d in _writer_quality(f"Here's the rewritten summary: {GOOD}")
    )


def test_exceeding_the_word_cap_is_a_defect():
    long = GOOD + " " + " ".join(["and delivered improvements"] * 8)
    assert any("over" in d for d in _writer_quality(long))


def test_a_verb_the_scorer_does_not_know_is_a_defect():
    """A rewrite whose verb the scorer will not credit leaves the finding open.

    This used to use "cut", which was genuinely absent from ACTION_VERBS --
    a scorer bug, not a writer defect, and since fixed. The example now uses
    "handled", which is weak on purpose and correctly earns no credit.
    """
    text = GOOD.replace("reduced", "handled").replace("leading", "with")
    assert any("ACTION_VERBS" in d for d in _writer_quality(text))


def test_quality_defects_do_not_disqualify():
    """A clumsy writer is a worse choice, not a disallowed one."""
    text = f"Here's the summary: {GOOD}"
    assert _writer_blockers("vendor/chat-model", text) is None
    assert _writer_quality(text)
