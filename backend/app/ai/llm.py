"""Model gateway.

One place that knows about UnoRouter. Per-task model routing, a bounded
fallback chain, and telemetry written to agent_runs.

The provider is OpenAI-compatible, so langchain-openai works by pointing
base_url at it. `gemini-3.5-flash-lite:free` averages ~9s end-to-end, so the
timeout is set just above the slowest observed model rather than "generous" --
a 90s timeout was measured burning the full 90s before failing.

Two constraints discovered by running this against the real provider, both of
which shape the code below: free models accept `json_schema` and reply with
prose anyway (see _salvage), and the free tier allows one request per minute
per model per account (see classify / RATE_LIMIT_WAIT_BUDGET_SECONDS).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

from app.ai import prose
from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Per-task routing (DESIGN.md §6.4). "cheap" and "strong" both resolve to the
# free model by default; paid identifiers are the documented upgrade path.
TASK_MODELS: dict[str, str] = {
    "extraction": settings.model_default,
    "jd_analysis": settings.model_default,
    "scoring": settings.model_default,
    "writing": settings.model_default,
    "critique": settings.model_default,
    "chat": settings.model_default,
}

# Free tier only. Ordered by measured reliability (uptime x success), not by
# reputation -- the point of a fallback is that it answers when the one above
# it did not.
#
#   k2-horizon        524K ctx   100.0% up   94.6% ok   208ms
#   gemini-3.5-...    1M   ctx    98.4% up   93.9% ok   13.05s   (default)
#   glm-4.7-flash     128K ctx   100.0% up   93.3% ok   7.02s
#   gemma-4-26b       262K ctx    96.1% up   93.5% ok   7.59s
#
# The two paid models that used to sit here (gemini-3.1-flash-lite,
# gemini-3-flash) were removed: with the default at 98.4% uptime this chain is
# reached often enough to produce a real bill nobody asked for.
# Per-task reasoning effort. gemini-3.5-flash-lite defaults to *medium* thinking,
# which the critic does not need: it compares two short strings and returns
# {approved, notes, severity}. Sending "minimal" for that one task cuts the
# thinking budget without changing model, schema support, or any other task.
#
# A task absent from this table sends no reasoning_effort at all, so its request
# payload is byte-identical to before. Deliberately not applied to "writing",
# which is genuinely generative.
TASK_EFFORT: dict[str, str] = {
    "critique": "minimal",
}


def effort_for(task: str) -> str | None:
    """Reasoning effort for a task, or None to leave the provider default."""
    return TASK_EFFORT.get(task)


FALLBACK_CHAIN: list[str] = [
    settings.model_default,
    "k2-horizon:free",
    "glm-4.7-flash:free",
    "gemma-4-26b:free",
]


def is_free(model: str) -> bool:
    """UnoRouter marks zero-cost models with a ':free' suffix."""
    return model.strip().endswith(":free")


def resolve_chain(task: str) -> list[str]:
    """The models to try for a task, nearest first.

    Filters paid models out when settings.free_models_only is set, so a paid
    name cannot reach the network by being typed into MODEL_DEFAULT or appended
    to the chain by a later edit.
    """
    preferred = TASK_MODELS.get(task, settings.model_default)
    chain = [preferred] + [m for m in FALLBACK_CHAIN if m != preferred]
    if not settings.free_models_only:
        return chain
    free = [m for m in chain if is_free(m)]
    dropped = [m for m in chain if not is_free(m)]
    if dropped:
        logger.warning(
            "free_models_only is on; skipping paid model(s): %s", ", ".join(dropped)
        )
    if not free:
        raise LLMUnavailable(
            "free_models_only is on but no free model is configured "
            f"(chain was: {', '.join(chain)}). Set a ':free' MODEL_DEFAULT "
            "or set FREE_MODELS_ONLY=false to allow paid models."
        )
    return free


class LLMUnavailable(RuntimeError):
    """No key configured, or every model in the chain failed."""


# The free tier allows one request per minute per model per account. A short
# "retry in 4s" is worth waiting out; a long one is not, because an interactive
# request must not hang. This budget is the total wait across one gateway call.
RATE_LIMIT_WAIT_BUDGET_SECONDS = 8.0

_RETRY_IN = re.compile(r"retry in (\d+(?:\.\d+)?)\s*s", re.I)


def _retry_after_seconds(exc: Exception) -> float | None:
    """Seconds the provider asked us to wait, from its 429 message."""
    match = _RETRY_IN.search(str(exc))
    return float(match.group(1)) if match else None


def classify(exc: Exception) -> str:
    """Bucket a failure so the trace distinguishes *kinds* of not-working.

    Rate limiting and provider flakiness are not model failures: a model that
    is throttled or whose upstream is briefly down has said nothing about its
    own quality. Recording them as plain errors is what let a transport problem
    masquerade as a quality verdict.
    """
    name = type(exc).__name__
    text = str(exc)
    status = getattr(exc, "status_code", None)

    if "RateLimit" in name or status == 429 or "Too many requests" in text:
        return "rate_limited"
    if "Timeout" in name or "timed out" in text.lower():
        return "timeout"
    if "upstream" in text.lower() or "temporarily unavailable" in text.lower():
        return "transient"
    if "ValidationError" in name or "json_invalid" in text or "Invalid JSON" in text:
        return "unparsable"
    return "error"


def _raw_text_from_exception(exc: Exception) -> str:
    """The offending model output carried inside a pydantic ValidationError.

    When json_schema is ignored and prose comes back, pydantic records the
    prose as the failing input. That is the model's actual answer, so it is
    worth one salvage attempt before moving down the chain.
    """
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return ""
    try:
        for entry in errors():
            value = entry.get("input")
            if isinstance(value, str) and value.strip():
                return value
    except Exception:  # noqa: BLE001 - salvage must never raise
        return ""
    return ""


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # some providers return content blocks
        return " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return "" if content is None else str(content)


def _salvage(text: str, schema: type[T]) -> tuple[T | None, str]:
    """Recover a schema instance from unstructured output.

    Tier 2: JSON hiding inside prose or a code fence -- lossless.
    Tier 4: a schema-declared prose reader, only for schemas that opt in.

    Returns the instance and the status to record, so a salvaged answer is
    never reported as though structured output had worked.
    """
    if not text:
        return None, "unparsable"

    payload = prose.extract_json(text)
    if payload is not None:
        for candidate in (payload, {"verdicts": payload}):
            if not isinstance(candidate, (dict, list)):
                continue
            try:
                return schema.model_validate(candidate), "ok-json"
            except Exception:  # noqa: BLE001 - try the next shape
                continue

    from_prose = getattr(schema, "from_prose", None)
    if callable(from_prose):
        try:
            recovered = from_prose(text)
        except Exception:  # noqa: BLE001 - salvage must never raise
            recovered = None
        if recovered is not None:
            return recovered, "ok-prose"

    return None, "unparsable"


@dataclass
class CallResult:
    """Outcome of one gateway call, including telemetry."""

    content: Any
    model: str
    latency_ms: int
    status: str = "ok"
    tokens_in: int = 0
    tokens_out: int = 0
    error: str = ""
    trace: list[dict] = field(default_factory=list)


def is_configured() -> bool:
    return settings.llm_configured


def _client(model: str, temperature: float = 0.2, reasoning_effort: str | None = None):
    from langchain_openai import ChatOpenAI

    # reasoning_effort is a named field on ChatOpenAI (str | None), so it must
    # be passed explicitly -- routing it through model_kwargs raises a conflict.
    # None omits it from the request body entirely.
    return ChatOpenAI(
        model=model,
        base_url=settings.unorouter_base_url,
        api_key=settings.unorouter_api_key,
        timeout=settings.llm_timeout_seconds,
        max_retries=0,  # the gateway owns retry policy, not the SDK
        temperature=temperature,
        reasoning_effort=reasoning_effort,
    )


def _usage(response: Any) -> tuple[int, int]:
    meta = getattr(response, "usage_metadata", None) or {}
    return int(meta.get("input_tokens", 0)), int(meta.get("output_tokens", 0))


def complete(
    task: str,
    system: str,
    user: str,
    temperature: float = 0.2,
) -> CallResult:
    """Plain text completion with a bounded fallback chain."""
    if not is_configured():
        raise LLMUnavailable("No UNOROUTER_API_KEY configured")

    from langchain_core.messages import HumanMessage, SystemMessage

    chain = resolve_chain(task)
    trace: list[dict] = []

    for model in chain:
        started = time.perf_counter()
        try:
            response = _client(model, temperature, effort_for(task)).invoke(
                [SystemMessage(content=system), HumanMessage(content=user)]
            )
            elapsed = int((time.perf_counter() - started) * 1000)
            tokens_in, tokens_out = _usage(response)
            trace.append({"model": model, "status": "ok", "latency_ms": elapsed})
            return CallResult(
                content=response.content,
                model=model,
                latency_ms=elapsed,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                trace=trace,
            )
        except Exception as exc:  # noqa: BLE001 — try the next model
            elapsed = int((time.perf_counter() - started) * 1000)
            kind = classify(exc)
            trace.append(
                {
                    "model": model,
                    "status": kind,
                    "latency_ms": elapsed,
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                }
            )
            logger.warning("LLM %s failed for task %s (%s): %s", model, task, kind, exc)

    raise LLMUnavailable(f"All models failed for task {task}: {trace}")


def structured(
    task: str,
    system: str,
    user: str,
    schema: type[T],
    temperature: float = 0.2,
) -> tuple[T, CallResult]:
    """Schema-constrained completion, with a salvage ladder underneath it.

    json_schema is still requested first and is still the only path that
    reports status "ok". Measured behaviour on the free tier, though, is that
    providers accept the schema and reply with markdown anyway, so rather than
    discard a correct answer over its envelope we try, in order:

        1. the provider's own parse                      -> "ok"
        2. JSON embedded in the raw text or a fence      -> "ok-json"
        3. a schema-declared prose reader, if it opts in -> "ok-prose"

    Each tier records a distinct status, so a run that only worked by salvaging
    is never indistinguishable from one where structured output worked.
    """
    if not is_configured():
        raise LLMUnavailable("No UNOROUTER_API_KEY configured")

    from langchain_core.messages import HumanMessage, SystemMessage

    chain = resolve_chain(task)
    trace: list[dict] = []
    wait_budget = RATE_LIMIT_WAIT_BUDGET_SECONDS

    for model in chain:
        attempts = 0
        while True:
            attempts += 1
            started = time.perf_counter()
            try:
                runnable = _client(
                    model, temperature, effort_for(task)
                ).with_structured_output(schema, method="json_schema", include_raw=True)
                envelope = runnable.invoke(
                    [SystemMessage(content=system), HumanMessage(content=user)]
                )
                elapsed = int((time.perf_counter() - started) * 1000)

                parsed = envelope.get("parsed") if isinstance(envelope, dict) else envelope
                raw = envelope.get("raw") if isinstance(envelope, dict) else None
                status = "ok"

                if parsed is None:
                    parsed, status = _salvage(_message_text(raw), schema)
                if parsed is None:
                    raise ValueError(
                        "structured output did not parse, and the raw "
                        "reply could not be salvaged"
                    )

                tokens_in, tokens_out = _usage(raw) if raw is not None else (0, 0)
                trace.append({"model": model, "status": status, "latency_ms": elapsed})
                if status != "ok":
                    logger.warning(
                        "LLM %s ignored json_schema for task %s; recovered via %s",
                        model, task, status,
                    )
                return parsed, CallResult(
                    content=parsed,
                    model=model,
                    latency_ms=elapsed,
                    status=status,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    trace=trace,
                )

            except Exception as exc:  # noqa: BLE001
                elapsed = int((time.perf_counter() - started) * 1000)

                # The provider may have answered correctly and merely failed
                # validation; pydantic kept the text it choked on.
                recovered, status = _salvage(_raw_text_from_exception(exc), schema)
                if recovered is not None:
                    trace.append(
                        {"model": model, "status": status, "latency_ms": elapsed}
                    )
                    logger.warning(
                        "LLM %s ignored json_schema for task %s; recovered via %s",
                        model, task, status,
                    )
                    return recovered, CallResult(
                        content=recovered,
                        model=model,
                        latency_ms=elapsed,
                        status=status,
                        trace=trace,
                    )

                kind = classify(exc)

                # A throttled model has told us nothing about itself. If it
                # asked for a short wait and we can afford it, wait rather
                # than burning the rest of the chain on the same limit.
                if kind == "rate_limited" and attempts == 1:
                    delay = _retry_after_seconds(exc)
                    if delay is not None and 0 < delay <= wait_budget:
                        wait_budget -= delay
                        trace.append(
                            {
                                "model": model,
                                "status": "rate_limited_waited",
                                "latency_ms": elapsed,
                                "waited_s": delay,
                            }
                        )
                        logger.info(
                            "LLM %s rate-limited on task %s; waiting %.0fs as asked",
                            model, task, delay,
                        )
                        time.sleep(delay)
                        continue  # one more attempt on the same model

                trace.append(
                    {
                        "model": model,
                        "status": kind,
                        "latency_ms": elapsed,
                        "error": f"{type(exc).__name__}: {exc}"[:200],
                    }
                )
                logger.warning("LLM %s failed for task %s (%s): %s", model, task, kind, exc)
            break  # next model in the chain

    raise LLMUnavailable(f"All models failed for task {task}: {trace}")


def embed_remote(texts: list[str]) -> list[list[float]]:
    """Provider embeddings. Reported ~74% success, so callers must catch."""
    if not is_configured():
        raise LLMUnavailable("No UNOROUTER_API_KEY configured")

    from langchain_openai import OpenAIEmbeddings

    client = OpenAIEmbeddings(
        model=settings.embedding_model,
        base_url=settings.unorouter_base_url,
        api_key=settings.unorouter_api_key,
        timeout=settings.llm_timeout_seconds,
    )
    return client.embed_documents(texts)
