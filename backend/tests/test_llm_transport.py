"""The chain walk, the retry policy and the salvage ladder, with no network.

`app/ai/llm.py` sat at 56% line coverage, and the uncovered block was
`structured()`'s whole `for model in chain:` loop -- it opens with
`if not is_configured(): raise`, so a key-less suite never reaches it.

That block is where this project's transport bugs came from:

  * a de-dup filter that removed only the preferred id, so `model_default`
    appearing later in FALLBACK_CHAIN was attempted twice in a row;
  * 410 Gone treated as a retryable error when it is a permanent config fact;
  * an empty `content` from a reasoning model counted as a successful reply;
  * a prose-only answer salvaged into a schema that cannot read prose.

None of it needs a provider. The seam is `_client()`: production calls
`_client(...).with_structured_output(...).invoke(...)`, so a stub standing in
for `_client` makes every branch deterministic and instant.
"""

from __future__ import annotations

import pytest

from app.ai import llm
from app.ai.schemas import CriticVerdict, RevisionDraft
from app.config import settings

GOOD = {"approved": True, "notes": "", "severity": "none"}


class _Response:
    """Minimal stand-in for the provider's raw message."""

    def __init__(self, text: str = ""):
        self.content = text
        self.response_metadata: dict = {}
        self.usage_metadata = {"input_tokens": 11, "output_tokens": 7}


class _Runnable:
    def __init__(self, stub, model):
        self._stub = stub
        self._model = model

    def with_structured_output(self, schema, method=None, include_raw=False):
        self._schema = schema
        return self

    def invoke(self, messages):
        return self._stub.answer(self._model)


class _Stub:
    """Scripted provider. Maps a model id to an outcome, and records calls.

    An outcome is either a callable raising an exception, or the envelope
    `with_structured_output(include_raw=True)` returns: {"parsed", "raw"}.
    """

    def __init__(self, script: dict):
        self.script = script
        self.calls: list[str] = []

    def __call__(self, model, temperature=0.2, reasoning_effort=None):
        return _Runnable(self, model)

    def answer(self, model):
        self.calls.append(model)
        outcome = self.script.get(model, self.script.get("*"))
        if outcome is None:
            raise AssertionError(f"stub had no script for {model}")
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return outcome()
        return outcome


class _Status(Exception):
    """An error carrying an HTTP status, like the provider SDKs raise."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


@pytest.fixture()
def keyed(_no_provider_keys, monkeypatch):
    """Re-arm one provider.

    Depends on the autouse key-clearing fixture by name so it is guaranteed to
    run *after* it; otherwise the ordering is unspecified and this silently
    does nothing.
    """
    monkeypatch.setattr(settings, "nvidia_api_key", "nvapi-test-key", raising=False)
    monkeypatch.setattr(settings, "groq_api_key", "gsk-test-key", raising=False)
    assert llm.is_configured() is True
    return True


@pytest.fixture()
def chain(monkeypatch):
    """A short, explicit chain so assertions name real ids."""
    ids = ["nvidia:a", "nvidia:b", "groq:c"]
    monkeypatch.setattr(llm, "resolve_chain", lambda task: list(ids))
    return ids


def _install(monkeypatch, script):
    stub = _Stub(script)
    monkeypatch.setattr(llm, "_client", stub)
    return stub


def _ok(payload=None):
    return {"parsed": CriticVerdict(**(payload or GOOD)), "raw": _Response("{}")}


# --------------------------------------------------------------------------
# chain walking
# --------------------------------------------------------------------------


def test_a_404_falls_through_and_reports_the_model_that_answered(
    keyed, chain, monkeypatch
):
    """`AgentOutcome.model` must name the answerer, not the preference.

    A report that prints the configured model while a fallback did the work
    attributes quality to the wrong thing.
    """
    stub = _install(
        monkeypatch,
        {"nvidia:a": _Status(404, "model not found"), "nvidia:b": _ok()},
    )
    _, result = llm.structured("critique", "sys", "usr", CriticVerdict)

    assert result.model == "nvidia:b"
    assert stub.calls == ["nvidia:a", "nvidia:b"]


def test_the_loop_attempts_each_chain_entry_at_most_once(keyed, chain, monkeypatch):
    """The loop itself does not revisit an entry.

    Note what this does NOT prove: it stubs `resolve_chain`, so it cannot
    catch a chain that arrives already containing a duplicate. That is a
    separate property, asserted against the real resolver in
    test_resolve_chain_never_returns_the_same_model_twice below -- a test
    that did not exist until this file, even though the de-dup bug was fixed
    days ago.
    """
    stub = _install(
        monkeypatch,
        {"nvidia:a": _Status(500, "boom"), "nvidia:b": _Status(500, "boom"),
         "groq:c": _ok()},
    )
    llm.structured("critique", "sys", "usr", CriticVerdict)

    assert stub.calls == sorted(set(stub.calls), key=stub.calls.index)
    assert len(stub.calls) == len(set(stub.calls))


def test_every_model_failing_raises_llm_unavailable(keyed, chain, monkeypatch):
    stub = _install(monkeypatch, {"*": _Status(500, "boom")})
    with pytest.raises(llm.LLMUnavailable):
        llm.structured("critique", "sys", "usr", CriticVerdict)
    assert len(stub.calls) == 3, "every model in the chain should be tried"


def test_no_key_raises_before_any_client_is_built(_no_provider_keys, monkeypatch):
    """Fails closed, and spends nothing doing it."""
    stub = _install(monkeypatch, {"*": _ok()})
    with pytest.raises(llm.LLMUnavailable):
        llm.structured("critique", "sys", "usr", CriticVerdict)
    assert stub.calls == [], "a key-less call must not reach the provider"


# --------------------------------------------------------------------------
# classify: what kind of not-working
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc,expected",
    [
        (_Status(410, "this model is at end of life"), "retired"),
        (_Status(402, "Payment Required"), "quota_exhausted"),
        (_Status(402, "insufficient balance for this request"), "quota_exhausted"),
        (_Status(429, "Too many requests"), "rate_limited"),
        (TimeoutError("request timed out"), "timeout"),
        (RuntimeError("upstream connect error"), "transient"),
        (ValueError("Invalid JSON in response"), "unparsable"),
        (RuntimeError("something else entirely"), "error"),
    ],
)
def test_classify_buckets_failures_by_kind(exc, expected):
    """Throttling says nothing about a model's quality; an error does.

    Collapsing these let a transport problem masquerade as a quality verdict.
    """
    assert llm.classify(exc) == expected


def test_an_exhausted_monthly_budget_is_not_a_rate_limit():
    """402 and 429 need opposite responses, so they cannot share a bucket.

    A 429 clears in seconds and `structured()` will wait it out on the same
    model. A 402 means the month's allowance is gone and waiting is useless;
    the only useful move is falling through to another vendor. codecraft's
    free plan is 1M tokens/month, which measurement puts inside the plausible
    range for heavy use -- so this is a case this project will actually hit,
    not a hypothetical.
    """
    assert llm.classify(_Status(402, "Payment Required")) == "quota_exhausted"
    assert llm.classify(_Status(402, "Payment Required")) != "rate_limited"
    # And it must not be a bare "error", or an exhausted budget on the 28th
    # reads identically to a model that cannot follow instructions.
    assert llm.classify(_Status(402, "Payment Required")) != "error"


def test_only_a_rate_limit_triggers_the_wait_and_retry():
    """Pins the behaviour that makes the distinction worth drawing."""
    import inspect
    src = inspect.getsource(llm.structured)
    assert 'kind == "rate_limited"' in src, (
        "the wait-and-retry branch changed; confirm quota_exhausted still "
        "falls straight through rather than sleeping"
    )
    assert 'kind == "quota_exhausted"' not in src


def test_a_retired_model_is_tried_once_and_not_retried(keyed, chain, monkeypatch):
    """410 Gone is permanent and is a CONFIG fact. Retrying cannot help.

    This project's writer returned 410 nine hours after a probe recorded it as
    the fastest usable model.
    """
    stub = _install(
        monkeypatch,
        {"nvidia:a": _Status(410, "end of life"), "nvidia:b": _ok()},
    )
    _, result = llm.structured("critique", "sys", "usr", CriticVerdict)

    assert stub.calls.count("nvidia:a") == 1
    assert [t["status"] for t in result.trace if t["model"] == "nvidia:a"] == [
        "retired"
    ]


# --------------------------------------------------------------------------
# rate limiting
# --------------------------------------------------------------------------


def test_a_short_retry_after_is_waited_out_on_the_same_model(
    keyed, chain, monkeypatch
):
    """Within budget, waiting beats burning the chain on the same limit."""
    slept: list[float] = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))

    seen = {"n": 0}

    def flaky():
        seen["n"] += 1
        if seen["n"] == 1:
            raise _Status(429, "Too many requests. Please retry in 2s")
        return _ok()

    stub = _install(monkeypatch, {"nvidia:a": flaky})
    _, result = llm.structured("critique", "sys", "usr", CriticVerdict)

    assert slept == [2.0]
    assert stub.calls == ["nvidia:a", "nvidia:a"], "retry stays on the same model"
    assert result.model == "nvidia:a"
    assert any(t["status"] == "rate_limited_waited" for t in result.trace)


def test_a_long_retry_after_moves_to_the_next_model(keyed, chain, monkeypatch):
    """Beyond the budget, waiting is worse than trying someone else."""
    slept: list[float] = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))

    over = llm.RATE_LIMIT_WAIT_BUDGET_SECONDS + 60
    _install(
        monkeypatch,
        {
            "nvidia:a": _Status(429, f"Too many requests. Please retry in {over}s"),
            "nvidia:b": _ok(),
        },
    )
    _, result = llm.structured("critique", "sys", "usr", CriticVerdict)

    assert slept == [], "must not block for longer than the budget"
    assert result.model == "nvidia:b"


# --------------------------------------------------------------------------
# the salvage ladder
# --------------------------------------------------------------------------


def test_structured_output_working_reports_status_ok(keyed, chain, monkeypatch):
    _install(monkeypatch, {"nvidia:a": _ok()})
    _, result = llm.structured("critique", "sys", "usr", CriticVerdict)
    assert result.status == "ok"


def test_json_in_a_markdown_fence_is_salvaged_as_ok_json(keyed, chain, monkeypatch):
    """A correct answer in the wrong envelope is still a correct answer --
    but it must never be indistinguishable from structured output working."""
    fenced = '```json\n{"approved": false, "notes": "adds AWS", "severity": "minor"}\n```'
    _install(
        monkeypatch,
        {"nvidia:a": {"parsed": None, "raw": _Response(fenced)}},
    )
    verdict, result = llm.structured("critique", "sys", "usr", CriticVerdict)

    assert result.status == "ok-json"
    assert verdict.approved is False


def test_prose_only_is_read_for_a_schema_that_opts_in(keyed, chain, monkeypatch):
    _install(
        monkeypatch,
        {"nvidia:a": {"parsed": None, "raw": _Response("REJECT - it adds a metric")}},
    )
    verdict, result = llm.structured("critique", "sys", "usr", CriticVerdict)

    assert result.status == "ok-prose"
    assert verdict.approved is False


def test_prose_only_fails_closed_for_a_schema_without_from_prose(
    keyed, chain, monkeypatch
):
    """The diffusiongemma failure, encoded.

    A model that writes good prose but cannot emit JSON produced `unparsable`
    on every writer case, because RevisionDraft has no `from_prose()`. Adding
    one naively is the wrong fix: that model's decline replies mix rule
    commentary ("...which violates Rule 1") into the text, and a reader taking
    the whole reply as `suggested_text` would paste that into a resume.
    """
    assert not callable(getattr(RevisionDraft, "from_prose", None)), (
        "If RevisionDraft gains from_prose(), it must detect refusals FIRST. "
        "Delete this guard deliberately, not incidentally."
    )
    _install(
        monkeypatch,
        {"*": {"parsed": None, "raw": _Response("Here is a better bullet: Led X.")}},
    )
    with pytest.raises(llm.LLMUnavailable):
        llm.structured("writing", "sys", "usr", RevisionDraft)


def test_an_empty_reply_is_a_failure_not_a_success(keyed, chain, monkeypatch):
    """A reasoning model can spend its whole budget thinking and return "".

    A diagnostic that accepted this printed "OK ... 0 words" and ranked the
    model as usable.
    """
    stub = _install(
        monkeypatch,
        {"nvidia:a": {"parsed": None, "raw": _Response("")}, "nvidia:b": _ok()},
    )
    _, result = llm.structured("critique", "sys", "usr", CriticVerdict)

    assert result.model == "nvidia:b", "empty content must not count as an answer"
    assert "nvidia:a" in stub.calls


def test_the_trace_records_every_attempt_in_order(keyed, chain, monkeypatch):
    """The trace is the only record of what a run actually did."""
    _install(
        monkeypatch,
        {
            "nvidia:a": _Status(410, "end of life"),
            "nvidia:b": _Status(429, "Too many requests"),
            "groq:c": _ok(),
        },
    )
    _, result = llm.structured("critique", "sys", "usr", CriticVerdict)

    assert [(t["model"], t["status"]) for t in result.trace] == [
        ("nvidia:a", "retired"),
        ("nvidia:b", "rate_limited"),
        ("groq:c", "ok"),
    ]


# --------------------------------------------------------------------------
# the real resolver
# --------------------------------------------------------------------------


def test_resolve_chain_never_returns_the_same_model_twice(keyed, monkeypatch):
    """The de-dup regression, against the real resolver.

    The first version of this test was WRONG and passed against the broken
    code. It set the preferred model equal to a later chain entry -- but the
    old `[m for m in FALLBACK_CHAIN if m != preferred]` filter removes those
    perfectly well. It only fails to see duplicates it is not comparing
    against.

    The real shape: FALLBACK_CHAIN's first entry is the *variable*
    `model_default`, and a later entry names the same id *literally*. So the
    duplicate lives inside FALLBACK_CHAIN, between two entries neither of
    which equals `preferred`, and a filter keyed on `preferred` cannot touch
    it. Live, that was

        nvidia:gpt-oss-20b -> groq:gpt-oss-120b -> groq:gpt-oss-120b -> ...

    burning a second identical attempt on a model that had just failed.
    """
    dup = "groq:openai/gpt-oss-120b"
    # entry 0 stands in for model_default; entry 1 names the same id.
    monkeypatch.setattr(llm, "FALLBACK_CHAIN", [dup, dup, "nvidia:ultra"])
    monkeypatch.setattr(llm, "TASK_MODELS", {"writing": "nvidia:openai/gpt-oss-20b"})
    monkeypatch.setattr(settings, "free_models_only", False, raising=False)

    produced = llm.resolve_chain("writing")

    assert len(produced) == len(set(produced)), f"duplicate in chain: {produced}"
    assert produced == ["nvidia:openai/gpt-oss-20b", dup, "nvidia:ultra"]


def test_resolve_chain_keeps_the_preferred_model_first(keyed, monkeypatch):
    """Order is the policy; de-duplication must not quietly reorder it."""
    monkeypatch.setattr(llm, "FALLBACK_CHAIN", ["nvidia:x", "groq:y"])
    monkeypatch.setattr(llm, "TASK_MODELS", {"critique": "nvidia:z"})
    monkeypatch.setattr(settings, "free_models_only", False, raising=False)

    assert llm.resolve_chain("critique") == ["nvidia:z", "nvidia:x", "groq:y"]
