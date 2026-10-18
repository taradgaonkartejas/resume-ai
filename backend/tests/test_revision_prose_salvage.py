"""RevisionDraft must survive a model that answers in plain text.

`nvidia:meta/muse-glimmer-30b` failed all ten writer-corpus cases with

    unparsable: Invalid JSON: expected value at line 1 column 1
    input_value='Reduced CI pipeline runtime from 22 minutes to 9 minutes.'

Every one of those payloads was a correct rewrite, discarded for not being
wrapped in JSON. Because RevisionDraft had no `from_prose()` the chain fell
all the way through to `groq:openai/gpt-oss-120b` -- the CRITIC's model --
so the writer was graded by its own model and the run was inconclusive.

The strings below are the real ones from that run.
"""

from __future__ import annotations

import pytest

from app.ai.schemas import RevisionDraft

# Verbatim from the failing run.
REAL = [
    "Reduced CI pipeline runtime from 22 minutes to 9 minutes.",
    "Managed cloud infrastructure across an estate of 40 services.",
    "Migrated 30 services from EC2 to EKS with zero customer downtime.",
    "Built reusable Terraform modules adopted for infrastructure business-wide.",
    "Led cloud infrastructure for an estate of 40 services.",
    "Built platforms for 6 years.",
]


@pytest.mark.parametrize("text", REAL, ids=lambda s: s[:34])
def test_a_real_prose_rewrite_is_recovered(text):
    got = RevisionDraft.from_prose(text)
    assert got is not None, "this exact string was thrown away in production"
    assert got.suggested_text == text


def test_the_recovered_draft_invents_no_rationale():
    """`reasoning` is shown to the user; a made-up one is a small lie."""
    got = RevisionDraft.from_prose(REAL[0])
    assert got.reasoning == ""
    assert got.keywords == []


@pytest.mark.parametrize(
    "text,why",
    [
        ("I cannot help with that request.", "refusal"),
        ("I'm unable to rewrite this without more detail.", "refusal"),
        ("As an AI language model, I should note...", "refusal"),
        ("Could you provide the original bullet?", "question"),
        ("What is the metric you want to highlight?", "question"),
        ('{"suggested_text": "Reduced runtime"}', "json the ok-json rung owns"),
        ('"suggested_text": "Reduced runtime"', "json fragment"),
        ("", "empty"),
        ("   \n  ", "whitespace"),
        ("Short.", "too short to be a rewrite"),
        ("First paragraph here.\n\nSecond paragraph of commentary.", "multi-para"),
        ("x" * 1200, "far too long"),
    ],
)
def test_anything_that_is_not_a_rewrite_is_refused(text, why):
    assert RevisionDraft.from_prose(text) is None, (
        f"accepted a {why} as replacement text -- this goes into a resume"
    )


def test_the_salvage_rung_is_reachable_through_the_parser():
    """End to end through the real ladder, not just the classmethod."""
    from app.ai import llm

    parsed, status = llm._salvage(
        "Reduced CI pipeline runtime from 22 minutes to 9 minutes.", RevisionDraft
    )
    assert status == "ok-prose", f"ladder returned {status!r}, not the prose rung"
    assert parsed.suggested_text.startswith("Reduced CI pipeline runtime")


def test_json_still_wins_over_prose():
    """Salvage must not shadow a correct structured answer."""
    from app.ai import llm

    parsed, status = llm._salvage(
        '{"suggested_text": "Cut build time to 9 minutes.", '
        '"keywords": ["CI"], "reasoning": "Adds the metric."}',
        RevisionDraft,
    )
    assert status in {"ok", "ok-json"}
    assert parsed.keywords == ["CI"]
    assert parsed.reasoning == "Adds the metric."


def test_every_writer_path_schema_now_has_a_salvage_rung():
    """The gap this closes, stated as an invariant.

    WriterOutput is excluded on purpose: it carries a LIST of drafts, and
    reading an arbitrary number of rewrites out of prose is guesswork of a
    different order. It keeps failing loudly.
    """
    assert callable(getattr(RevisionDraft, "from_prose", None))


def test_the_exact_production_exception_now_recovers():
    """Reproduce the real failure, not an approximation of it.

    In the live run the prose never reached `_salvage` as a plain string --
    it arrived inside a pydantic ValidationError raised by
    `with_structured_output`, and `llm.structured` recovers it via
    `_raw_text_from_exception` (llm.py:751). That plumbing already existed;
    it simply had nothing to call, because RevisionDraft had no `from_prose`.

    This builds the identical exception and asserts the whole path.
    """
    import pydantic

    from app.ai import llm

    answer = "Reduced CI pipeline runtime from 22 minutes to 9 minutes."
    try:
        RevisionDraft.model_validate_json(answer)
    except pydantic.ValidationError as exc:
        caught = exc
    else:
        pytest.fail("expected prose to fail json validation")

    assert llm.classify(caught) == "unparsable"

    raw = llm._raw_text_from_exception(caught)
    assert raw == answer, "the model's real answer was not recoverable"

    recovered, status = llm._salvage(raw, RevisionDraft)
    assert status == "ok-prose"
    assert recovered.suggested_text == answer


def test_without_the_salvage_rung_this_was_a_total_loss():
    """Characterise the old behaviour, so the regression is legible.

    A schema with no `from_prose` returns (None, 'unparsable') -- the whole
    call is lost and the chain falls through. For `writing` that meant
    landing on the critic's own model.
    """
    from app.ai import llm
    from app.ai.schemas import WriterOutput

    recovered, status = llm._salvage(
        "Reduced CI pipeline runtime from 22 minutes to 9 minutes.",
        WriterOutput,
    )
    assert recovered is None and status == "unparsable"
