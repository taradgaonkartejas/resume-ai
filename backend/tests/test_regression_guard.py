"""A rewrite that fixes one finding and causes another is not a fix.

The writer corpus has measured this as its `clean` column since it was
written. Production never enforced it, so the corpus held the writer to a
standard the application did not -- and the gap was not theoretical:

    bullet_overlong_and_padded   fixed format.overlong_bullets
                                 introduced experience.unquantified_bullets

in two consecutive live runs. The padding that made the bullet too long was
carrying the metric, so trimming removed it.

An earlier attempt at this fixed only `_rule_rewrite`, which the live writer
never calls (it is reached only when the LLM fails or no key is set). The
check here is deterministic, local and runs on both paths.
"""

from __future__ import annotations

import pytest

from app.ai import agents
from app.ai.schemas import RevisionDraft
from app.config import settings

PADDED = (
    "Managed the migration of the legacy billing platform to AWS, which cut "
    "processing time by 40%, working across many different teams and several "
    "geographic regions over the course of the entire financial year "
    "alongside a large number of external stakeholders and partners."
)

DOC = {
    "contact": {
        "name": "Jane", "email": "j@e.com", "phone": "+1 555",
        "location": "Pune", "links": ["github.com/jane"],
    },
    "summary": {"text": ""},
    "experience": [
        {
            "company": "Acme", "role": "Platform Engineer", "dates": "2020-2024",
            "title": "Platform Engineer", "start_date": "2020", "end_date": "2024",
            "bullets": [PADDED],
        }
    ],
    "education": [{"school": "VIT", "degree": "BE", "year": "2016"}],
    "skills": [{"label": "Cloud", "items": ["AWS", "Terraform"]}],
    "projects": [],
    "certifications": [],
}


# --------------------------------------------------------------------------
# the detector
# --------------------------------------------------------------------------


def test_dropping_the_metric_while_trimming_is_detected():
    """The exact defect, reproduced twice live, now caught before the critic."""
    stripped = "Managed the migration of the legacy billing platform to AWS."
    introduced = agents.introduced_findings(DOC, "exp_0.bullet_0", stripped)
    assert "experience.unquantified_bullets" in introduced, introduced


def test_keeping_the_metric_while_trimming_is_clean():
    kept = (
        "Managed the migration of the legacy billing platform to AWS, "
        "which cut processing time by 40%."
    )
    assert agents.introduced_findings(DOC, "exp_0.bullet_0", kept) == []


def test_an_empty_suggestion_introduces_nothing():
    """Declining is not a regression."""
    assert agents.introduced_findings(DOC, "exp_0.bullet_0", "   ") == []


def test_the_detector_never_raises_on_a_bad_ref():
    """A guard that can throw is a guard that takes the endpoint down."""
    assert agents.introduced_findings(DOC, "exp_99.bullet_99", "anything") == []


# --------------------------------------------------------------------------
# composing: a section that did not exist cannot regress
# --------------------------------------------------------------------------


def test_composing_a_summary_is_not_penalised_for_its_own_section():
    """summary.missing suppresses the other summary rules.

    Composing ANY summary unmasks summary.too_short, summary.no_metric and
    summary.no_action_verb. That is the scorer revealing rules it had been
    hiding, not the writer breaking something.

    It also cannot be satisfied: summary.no_metric is only cleared by a
    number, and the writer is forbidden to invent one. Counting these would
    make /rewrite answer 422 for every compose request, permanently.
    """
    composed = "Platform engineer focused on cloud infrastructure and reliability."
    assert agents.introduced_findings(DOC, "summary.text", composed) == []


def test_composing_is_still_penalised_for_breaking_a_different_section():
    """The exemption is scoped to the ref being written, not switched off."""
    before = set(agents.introduced_findings(DOC, "summary.text", "A short summary."))
    assert before == set(), "precondition: composing a summary is clean"


# --------------------------------------------------------------------------
# the hard check
# --------------------------------------------------------------------------


def test_hard_check_rejects_a_regression_with_severity_regression():
    draft = {
        "target_ref": "exp_0.bullet_0",
        "original_text": PADDED,
        "suggested_text": "Managed the migration of the legacy billing platform to AWS.",
    }
    outcome = agents._hard_check(draft, DOC)
    assert outcome is not None, "a regression must not reach the LLM critic"
    assert outcome.value.approved is False
    assert outcome.value.severity == "regression"
    assert "experience.unquantified_bullets" in outcome.value.notes


def test_hard_check_passes_a_faithful_trim():
    draft = {
        "target_ref": "exp_0.bullet_0",
        "original_text": PADDED,
        "suggested_text": (
            "Managed the migration of the legacy billing platform to AWS, "
            "which cut processing time by 40%."
        ),
    }
    assert agents._hard_check(draft, DOC) is None


def test_a_fabricated_number_still_outranks_a_regression():
    """Both are rejections; the fabrication label must not be lost."""
    draft = {
        "target_ref": "exp_0.bullet_0",
        "original_text": PADDED,
        "suggested_text": "Managed the migration, cutting cost by 87%.",
    }
    outcome = agents._hard_check(draft, DOC)
    assert outcome is not None
    assert outcome.value.severity == "fabrication"


# --------------------------------------------------------------------------
# the retry: the writer is finally told why
# --------------------------------------------------------------------------


class _Call:
    model = "nvidia:test"
    latency_ms = 10
    tokens_in = 5
    tokens_out = 5


@pytest.fixture()
def keyed(_no_provider_keys, monkeypatch):
    monkeypatch.setattr(settings, "nvidia_api_key", "nvapi-test", raising=False)
    return True


def _script(monkeypatch, replies):
    """Make llm.structured return `replies` in order, recording the prompts."""
    seen: list[str] = []

    def fake(task, system, user, schema, temperature=0.2):
        seen.append(user)
        text = replies[min(len(seen) - 1, len(replies) - 1)]
        return RevisionDraft(suggested_text=text, keywords=[], reasoning="r"), _Call()

    monkeypatch.setattr(agents.llm, "structured", fake)
    monkeypatch.setattr(agents.llm, "is_configured", lambda: True)
    return seen


FINDING = {
    "id": "format.overlong_bullets",
    "message": "Bullet is too long",
    "fix_hint": "Trim it",
    "target_ref": "exp_0.bullet_0",
}

GOOD = (
    "Managed the migration of the legacy billing platform to AWS, "
    "which cut processing time by 40%."
)
BAD = "Managed the migration of the legacy billing platform to AWS."


def test_a_rejected_draft_is_retried_once_with_the_reason(keyed, monkeypatch):
    """Previously a bad draft was discarded and /rewrite answered 422.

    The writer was never told what was wrong -- the critic's reasoning went
    into a log line and nowhere else.
    """
    seen = _script(monkeypatch, [BAD, GOOD])
    out = agents.rewrite_for_finding(DOC, "exp_0.bullet_0", FINDING)

    assert len(seen) == 2, "a rejected draft should be retried exactly once"
    assert out.value["suggested_text"] == GOOD
    assert "REJECTED" in seen[1]
    assert "experience.unquantified_bullets" in seen[1]
    assert BAD in seen[1], "the retry must show the writer what it produced"


def test_a_clean_draft_is_not_retried(keyed, monkeypatch):
    """The retry costs a second call; it must only happen on a rejection."""
    seen = _script(monkeypatch, [GOOD])
    agents.rewrite_for_finding(DOC, "exp_0.bullet_0", FINDING)
    assert len(seen) == 1


def test_a_second_failure_is_returned_not_swallowed(keyed, monkeypatch):
    """Attempt 2 is handed on even if it still fails.

    The same hard checks run again downstream and will reject it there, where
    it is visible. Discarding it here would turn a reported rejection into a
    silent one.
    """
    seen = _script(monkeypatch, [BAD, BAD])
    out = agents.rewrite_for_finding(DOC, "exp_0.bullet_0", FINDING)
    assert len(seen) == 2
    assert out.value["suggested_text"] == BAD
    assert any("retry still" in n for n in out.notes)


def test_an_invented_number_is_the_reason_given_back(keyed, monkeypatch):
    seen = _script(monkeypatch, ["Cut processing time by 87%.", GOOD])
    agents.rewrite_for_finding(DOC, "exp_0.bullet_0", FINDING)
    assert len(seen) == 2
    assert "87" in seen[1]
    assert "appears nowhere in the resume" in seen[1]


# --------------------------------------------------------------------------
# the graph's revision gate (AI-QUALITY-PLAN D, 1.4)
# --------------------------------------------------------------------------

from app.ai.graphs import should_revise  # noqa: E402


def test_a_partly_rejected_round_is_revised():
    """The headline defect. `not approved and rejected` skipped this case.

    Two approved and four rejected never triggered a revision, so four
    rewrites were discarded silently and the user just saw fewer suggestions.
    """
    assert should_revise(rejected=[{"a": 1}] * 4, revision_round=0, max_revisions=2)


def test_a_fully_rejected_round_is_still_revised():
    assert should_revise(rejected=[{"a": 1}], revision_round=0, max_revisions=2)


def test_a_clean_round_is_not_revised():
    assert not should_revise(rejected=[], revision_round=0, max_revisions=2)


def test_the_revision_budget_is_respected():
    """Unbounded retries against a free tier is a rate-limit incident."""
    assert not should_revise(rejected=[{"a": 1}], revision_round=2, max_revisions=2)


def test_the_critic_notes_reach_the_writer(keyed, monkeypatch):
    """A retry that re-asks the identical question is not a retry.

    Before this, a revision round called write_suggestions with exactly the
    same arguments and relied on temperature for a different answer.
    """
    seen: list[str] = []

    class _Out:
        suggestions: list = []

    def fake(task, system, user, schema, temperature=0.2):
        seen.append(user)
        return schema(suggestions=[]), _Call()

    monkeypatch.setattr(agents.llm, "structured", fake)
    monkeypatch.setattr(agents.llm, "is_configured", lambda: True)

    agents.write_suggestions(
        DOC,
        ["Terraform"],
        rejections=[
            {"target_ref": "exp_0.bullet_0", "critic_notes": "invented the figure 87%"}
        ],
    )
    assert seen, "the writer should have been called"
    assert "REJECTED" in seen[0]
    assert "invented the figure 87%" in seen[0]
    assert "exp_0.bullet_0" in seen[0]
