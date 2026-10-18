"""A chain must reach a model that answers before the browser gives up.

Reported from the running app, creating a tailored resume:

    LLM codecraft:claude-sonnet-5 failed for task writing (timeout)
    LLM nvidia:nvidia/nemotron-3-ultra-550b-a55b failed for task writing (timeout)
    Request timed out after 90000 ms

Three separate defects stacked up:

 1. `nvidia:nvidia/nemotron-3-ultra-550b-a55b` was measured timing out at
    45,187ms on a live scoring call on 2026-10-11 and dropped as MODEL_WRITING
    -- but left in the shared FALLBACK_CHAIN.

 2. The `writing` demotion removes the critic's model from slot 2, which
    PROMOTED that dead model into it. So the first writer failure was followed
    by a second guaranteed timeout before anything that answers.

 3. The client budget and the server budget were independent numbers. The
    server spends up to llm_timeout_seconds PER MODEL across a chain; the
    browser aborted at a flat 90s. With a stale .env setting
    LLM_TIMEOUT_SECONDS=90 the client could not outlast even ONE attempt.

Deleting the model outright was tried first and correctly broke two tests:
it is the chain's only non-Groq entry, so a Groq 429 would have had nowhere
to go. That assertion had rotted into a proxy -- a dead NVIDIA id satisfies
"spans providers" by name while providing no fallback at all -- so the model
is kept for that case and pushed to the BACK of every chain instead.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.ai import llm
from app.ai.llm import TASK_MODELS, resolve_chain
from app.config import Settings, settings

TASKS = ("writing", "chat", "critique", "extraction", "jd_analysis", "scoring", "ats")


def _all_keys(monkeypatch):
    for p in llm.PROVIDERS.values():
        monkeypatch.setattr(settings, p.key_setting, f"{p.name}-key-xxxxxxxxxxxx")


# ---------------------------------------------------------------------------
# slow models are kept, but never in front of one that works


def test_a_slow_model_is_never_tried_before_a_working_one(monkeypatch):
    """Slow models go behind every model that answers -- with one exception.

    The writing chain deliberately parks the CRITIC's model dead last to
    protect writer/critic independence, and that rule outranks this one. So
    the critic is excluded from the comparison for `writing` rather than the
    assertion being loosened for every task: stated as "slow goes last" it
    would be false by design, and a test that is false by design gets
    weakened until it means nothing.
    """
    _all_keys(monkeypatch)
    for task in TASKS:
        chain = resolve_chain(task)
        exempt = {TASK_MODELS["critique"]} if task == "writing" else set()
        ranked = [m for m in chain if m not in exempt]
        slow = [i for i, m in enumerate(ranked) if llm.is_slow(m)]
        fast = [i for i, m in enumerate(ranked) if not llm.is_slow(m)]
        if slow and fast:
            assert min(slow) > max(fast), (
                f"{task}: a measured-slow model is ahead of a working one: {chain}"
            )


def test_the_second_writing_attempt_is_not_a_slow_model(monkeypatch):
    """The exact shape of the reported failure.

    Slot 2 is where the critic demotion used to deposit the dead NVIDIA
    model, so a writer timeout was followed by another one.
    """
    _all_keys(monkeypatch)
    chain = resolve_chain("writing")
    assert len(chain) > 1
    assert not llm.is_slow(chain[1]), (
        f"the second writing attempt is a known-slow model: {chain}"
    )


def test_deprioritising_runs_before_the_critic_demotion(monkeypatch):
    """Order of the two rules, which is load-bearing.

    Pushing slow models back AFTER demoting the critic would park a dead
    model behind the critic and re-break writer independence.
    """
    _all_keys(monkeypatch)
    chain = resolve_chain("writing")
    critic = TASK_MODELS["critique"]
    assert chain[-1] == critic, f"the critic must stay last: {chain}"
    slow = [m for m in chain if llm.is_slow(m)]
    if slow:
        assert chain.index(slow[-1]) < chain.index(critic)


def test_slow_is_not_retired(monkeypatch):
    """Two tiers, kept apart.

    RETIRED means never call it. SLOW means call it only as a last resort.
    Collapsing them would either delete the chain's vendor diversity or
    reinstate a model that cannot answer at all.
    """
    assert not (set(llm.SLOW_MODELS) & set(llm.RETIRED_MODELS))


def test_every_slow_entry_records_a_measurement():
    for model, reason in llm.SLOW_MODELS.items():
        assert len(reason) > 30, f"{model} has no substantive reason"
        assert any(ch.isdigit() for ch in reason), (
            f"{model}: a latency claim with no number is an opinion"
        )


# ---------------------------------------------------------------------------
# the control that was missing when RETIRED_MODELS was introduced


def test_no_retired_model_sits_in_the_shared_fallback_chain():
    """The gap that let this bug survive.

    RETIRED_MODELS was added with a test that `.env.example` may not name a
    retired model -- and no test that the chain SHIPPED in llm.py is clean.
    The mechanism existed and the hard-coded chain was never checked against
    it.
    """
    bad = [m for m in llm.FALLBACK_CHAIN if llm.is_retired(m)]
    assert not bad, f"retired models in FALLBACK_CHAIN: {bad}"


def test_no_task_default_is_a_retired_model():
    bad = {t: m for t, m in TASK_MODELS.items() if llm.is_retired(m)}
    assert not bad, f"retired models configured as task defaults: {bad}"


# ---------------------------------------------------------------------------
# the client must outlast the server


def _client_budget_ms() -> int:
    """The browser's budget for LLM-backed requests, read from the source."""
    env_ts = Path(__file__).resolve().parents[2] / "frontend/src/lib/env.ts"
    text = env_ts.read_text(encoding="utf-8")
    m = re.search(r"LLM_REQUEST_TIMEOUT_MS\s*=\s*([0-9_]+)", text)
    assert m, "LLM_REQUEST_TIMEOUT_MS is not defined in frontend/src/lib/env.ts"
    return int(m.group(1).replace("_", ""))


def test_the_client_outlasts_the_servers_worst_case(monkeypatch):
    """The two budgets must be RELATED, not independently chosen.

    A client that aborts mid-chain turns a slow success into a failure and
    the retry pays for the whole chain again. Compared against the shipped
    default rather than `settings`, because the suite loads the developer's
    real backend/.env -- which is exactly where the bad value (90) was.
    """
    _all_keys(monkeypatch)
    per_call = Settings.model_fields["llm_timeout_seconds"].default
    longest = max(len(resolve_chain(t)) for t in TASKS)
    worst_case_ms = per_call * longest * 1000

    assert _client_budget_ms() > worst_case_ms, (
        f"client gives up at {_client_budget_ms()}ms but the server can spend "
        f"{worst_case_ms}ms ({per_call}s x {longest} models). The browser "
        f"would abort before the chain is exhausted."
    )


def test_one_attempt_alone_cannot_exhaust_the_client_budget():
    """The specific trap in the user's .env.

    LLM_TIMEOUT_SECONDS=90 against a 90s client budget meant the browser gave
    up at the instant the FIRST model's timeout expired -- no fallback could
    ever be reached. Any per-call value at or above the client budget is a
    misconfiguration, so pin the shipped default well under it.
    """
    per_call_ms = Settings.model_fields["llm_timeout_seconds"].default * 1000
    assert per_call_ms * 2 <= _client_budget_ms(), (
        "the default per-call timeout leaves no room for a single fallback"
    )
