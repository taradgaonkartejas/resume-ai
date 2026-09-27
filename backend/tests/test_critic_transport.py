"""The transport under the critic: salvage, batching, and failure kinds.

These cover the three faults found by running the corpus against the real
provider, none of which the offline suite could see:

  1. free models accept json_schema and reply with markdown anyway, and the
     correct verdict inside that markdown was being thrown away
  2. the free tier allows one request per minute per model per account, so a
     per-draft critic is throttled into being the rule engine
  3. throttling, timeouts and provider outages were all recorded as plain
     "error", which is how a transport failure came to be reported as a
     quality verdict
"""

from unittest.mock import patch

import pytest

from app.ai import agents, llm
from app.ai.prose import extract_json, read_verdict, split_numbered
from app.ai.schemas import CriticBatch, CriticVerdict

DATA = {
    "experience": [
        {
            "company": "Acme",
            "role": "Eng",
            "dates": "",
            "bullets": [
                "Built Terraform modules adopted by 8 teams.",
                "Helped to work on improving the CI system somewhat.",
            ],
        }
    ]
}


def _draft(index: int, suggested: str) -> dict:
    return {
        "target_ref": f"exp_0.bullet_{index}",
        "original_text": DATA["experience"][0]["bullets"][index],
        "suggested_text": suggested,
        "keywords": [],
    }


# --------------------------------------------------------------------------
# 1. reading a verdict out of prose
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("**Rejected.** The rewrite claims ownership.", False),
        ("**Verdict:** Reject", False),
        ("**Decision: Reject**", False),
        ("**REJECT**", False),
        ("**Review: Reject**", False),
        ("**Verdict: Approved**", True),
        ("**Decision: Approve**", True),
        ("Approve", True),
        ("**Approved** - the rewrite is faithful.", True),
    ],
)
def test_real_provider_replies_are_readable(text, expected):
    """Every one of these is a verbatim shape seen from the live provider."""
    assert read_verdict(text) is expected


def test_a_negated_approval_is_a_rejection():
    """The one misparse that would ship a fabrication.

    "I cannot approve this" contains "approve"; reading it as an approval is
    the single most dangerous mistake this module could make.
    """
    assert read_verdict("I cannot approve this proposal because ...") is False
    assert read_verdict("I am unable to approve the rewrite.") is False
    assert read_verdict("I would not approve this.") is False


@pytest.mark.parametrize(
    "text",
    [
        "I cannot fulfill this request.",
        "Keywords the writer claims to have added: x, or invented metrics.",
        "",
        "The bullet mentions Kubernetes and Istio.",
    ],
)
def test_unreadable_output_fails_closed(text):
    """No verdict is better than a guessed one: None sends us to the rules."""
    assert read_verdict(text) is None
    assert CriticVerdict.from_prose(text) is None


def test_a_prose_rejection_keeps_its_reason_and_severity():
    verdict = CriticVerdict.from_prose(
        "**Reject**\n\nThe rewrite invents a metric not present in the original."
    )
    assert verdict is not None
    assert verdict.approved is False
    assert verdict.severity == "fabrication"
    assert "invents a metric" in verdict.notes


# --------------------------------------------------------------------------
# 2. recovering JSON that is merely wrapped
# --------------------------------------------------------------------------


def test_json_is_recovered_from_a_code_fence():
    assert extract_json('Sure:\n```json\n{"approved": true}\n```') == {"approved": True}


def test_json_is_recovered_from_surrounding_commentary():
    found = extract_json('Here is my verdict {"approved": false, "notes": "a}b"} done')
    assert found == {"approved": False, "notes": "a}b"}


def test_nothing_is_invented_when_there_is_no_json():
    assert extract_json("**Reject** - this invents a metric.") is None
    assert extract_json("") is None


def test_a_batch_reply_splits_on_its_numbering():
    assert [i for i, _ in split_numbered("[1] Reject ...\n[2] Approve ...")] == [1, 2]
    assert split_numbered("no numbering here") == []


def test_a_partially_readable_batch_is_refused():
    """All or nothing: half a set of verdicts cannot be matched to drafts."""
    assert CriticBatch.from_prose("[1] Reject, it invents a metric.\n[2] Hmm.") is None


def test_an_unnumbered_single_verdict_still_reads():
    batch = CriticBatch.from_prose("**Reject** - invents a metric")
    assert batch is not None
    assert len(batch.verdicts) == 1
    assert batch.verdicts[0].index == 1


# --------------------------------------------------------------------------
# 3. the gateway's salvage ladder and failure taxonomy
# --------------------------------------------------------------------------


def test_salvage_prefers_json_over_prose():
    """A model that emits JSON in markdown is recovered losslessly."""
    value, status = llm._salvage(
        '```json\n{"approved": false, "notes": "x"}\n```', CriticVerdict
    )
    assert status == "ok-json"
    assert value.approved is False


def test_salvage_falls_back_to_prose_and_says_so():
    value, status = llm._salvage("**Reject** - invents a metric", CriticVerdict)
    assert status == "ok-prose"
    assert value.approved is False


def test_salvage_reports_failure_rather_than_guessing():
    value, status = llm._salvage("I cannot fulfill this request.", CriticVerdict)
    assert value is None
    assert status == "unparsable"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Error code: 429 - Too many requests. retry in 23s", "rate_limited"),
        ("Request timed out.", "timeout"),
        (
            'k2 400: {"detail":"503: The selected upstream is temporarily unavailable."}',
            "transient",
        ),
        ("1 validation error for CriticVerdict Invalid JSON", "unparsable"),
        ("something else entirely", "error"),
    ],
)
def test_failures_are_classified_by_kind(message, expected):
    """Throttling is not a model failure and must not read like one."""
    assert llm.classify(Exception(message)) == expected


def test_the_providers_retry_hint_is_read():
    exc = Exception("Too many requests ... nothing is used up, retry in 23s. The paid ...")
    assert llm._retry_after_seconds(exc) == 23.0
    assert llm._retry_after_seconds(Exception("no hint here")) is None


# --------------------------------------------------------------------------
# 4. batching: one call for the whole set
# --------------------------------------------------------------------------


def test_six_drafts_cost_one_llm_call():
    """The rate limit is per minute, so per-draft calls cannot work."""
    drafts = [_draft(0, f"Drove Terraform adoption across 8 teams, v{i}.") for i in range(6)]
    batch = CriticBatch.model_validate(
        {"verdicts": [{"index": i + 1, "approved": True} for i in range(6)]}
    )

    with (
        patch.object(llm, "is_configured", return_value=True),
        patch.object(
            llm,
            "structured",
            return_value=(batch, llm.CallResult(content=batch, model="m", latency_ms=10)),
        ) as call,
    ):
        outcomes = agents.review_batch(drafts, DATA)

    assert call.call_count == 1
    assert len(outcomes) == 6
    assert all(o.value.approved for o in outcomes)


def test_the_batch_call_carries_every_draft():
    drafts = [_draft(0, "Owned Terraform."), _draft(1, "Improved the CI system.")]
    batch = CriticBatch.model_validate(
        {"verdicts": [{"index": 1, "approved": True}, {"index": 2, "approved": False}]}
    )
    with (
        patch.object(llm, "is_configured", return_value=True),
        patch.object(
            llm,
            "structured",
            return_value=(batch, llm.CallResult(content=batch, model="m", latency_ms=10)),
        ) as call,
    ):
        outcomes = agents.review_batch(drafts, DATA)

    payload = call.call_args.kwargs["user"]
    assert "[1]" in payload and "[2]" in payload
    assert "Owned Terraform." in payload
    assert "Improved the CI system." in payload
    assert outcomes[0].value.approved is True
    assert outcomes[1].value.approved is False


def test_a_verdict_count_mismatch_degrades_rather_than_misaligning():
    """Mapping the wrong verdict onto a draft is worse than using the rules."""
    drafts = [_draft(0, "Owned Terraform."), _draft(1, "Improved the CI system.")]
    short = CriticBatch.model_validate({"verdicts": [{"index": 1, "approved": True}]})
    with (
        patch.object(llm, "is_configured", return_value=True),
        patch.object(
            llm,
            "structured",
            return_value=(short, llm.CallResult(content=short, model="m", latency_ms=10)),
        ),
    ):
        outcomes = agents.review_batch(drafts, DATA)

    assert all(o.status == "degraded" for o in outcomes)


def test_hard_checks_never_reach_the_model():
    """An unresolvable ref is decided locally and must not spend a call."""
    bad = {
        "target_ref": "exp_9.bullet_9",
        "original_text": "nope",
        "suggested_text": "nope",
        "keywords": [],
    }
    with (
        patch.object(llm, "is_configured", return_value=True),
        patch.object(llm, "structured") as call,
    ):
        outcomes = agents.review_batch([bad], DATA)

    call.assert_not_called()
    assert outcomes[0].value.approved is False
    assert outcomes[0].value.severity == "fabrication"


def test_salvaged_verdicts_are_labelled_not_laundered():
    """A run carried by prose salvage must not look like a clean one."""
    batch = CriticBatch.model_validate({"verdicts": [{"index": 1, "approved": False}]})
    result = llm.CallResult(content=batch, model="m", latency_ms=10, status="ok-prose")
    with (
        patch.object(llm, "is_configured", return_value=True),
        patch.object(llm, "structured", return_value=(batch, result)),
    ):
        outcome = agents.review_draft(_draft(0, "Owned Terraform."), DATA)

    assert outcome.status == "ok-prose"
    assert any("salvage" in note for note in outcome.notes)


def test_review_draft_is_review_batch_of_one():
    """One code path, so the corpus measures what production runs."""
    draft = _draft(0, "Drove Terraform adoption across 8 teams.")
    single = agents.review_draft(draft, DATA).value
    assert single == agents.review_batch([draft], DATA)[0].value
