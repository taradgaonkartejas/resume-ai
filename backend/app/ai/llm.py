"""Model gateway.

One place that knows about UnoRouter. Per-task model routing, a bounded
fallback chain, and telemetry written to agent_runs.

The provider is OpenAI-compatible, so langchain-openai works by pointing
base_url at it. `gemini-3.5-flash-lite:free` averages ~10s latency with ~79%
uptime, so timeouts are generous and every call is wrapped.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

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

FALLBACK_CHAIN: list[str] = [
    settings.model_default,
    "gemini-3.1-flash-lite",
    "gemini-3-flash",
]


class LLMUnavailable(RuntimeError):
    """No key configured, or every model in the chain failed."""


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


def _client(model: str, temperature: float = 0.2):
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        base_url=settings.unorouter_base_url,
        api_key=settings.unorouter_api_key,
        timeout=settings.llm_timeout_seconds,
        max_retries=0,  # the gateway owns retry policy, not the SDK
        temperature=temperature,
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

    preferred = TASK_MODELS.get(task, settings.model_default)
    chain = [preferred] + [m for m in FALLBACK_CHAIN if m != preferred]
    trace: list[dict] = []

    for model in chain:
        started = time.perf_counter()
        try:
            response = _client(model, temperature).invoke(
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
            trace.append(
                {
                    "model": model,
                    "status": "error",
                    "latency_ms": elapsed,
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                }
            )
            logger.warning("LLM %s failed for task %s: %s", model, task, exc)

    raise LLMUnavailable(f"All models failed for task {task}: {trace}")


def structured(
    task: str,
    system: str,
    user: str,
    schema: type[T],
    temperature: float = 0.2,
) -> tuple[T, CallResult]:
    """Schema-constrained completion.

    Uses json_schema mode so the provider enforces the shape; a malformed
    response raises rather than silently returning prose.
    """
    if not is_configured():
        raise LLMUnavailable("No UNOROUTER_API_KEY configured")

    from langchain_core.messages import HumanMessage, SystemMessage

    preferred = TASK_MODELS.get(task, settings.model_default)
    chain = [preferred] + [m for m in FALLBACK_CHAIN if m != preferred]
    trace: list[dict] = []

    for model in chain:
        started = time.perf_counter()
        try:
            runnable = _client(model, temperature).with_structured_output(
                schema, method="json_schema", include_raw=True
            )
            envelope = runnable.invoke(
                [SystemMessage(content=system), HumanMessage(content=user)]
            )
            elapsed = int((time.perf_counter() - started) * 1000)
            parsed = envelope.get("parsed") if isinstance(envelope, dict) else envelope
            if parsed is None:
                raise ValueError("structured output did not parse")
            raw = envelope.get("raw") if isinstance(envelope, dict) else None
            tokens_in, tokens_out = _usage(raw) if raw is not None else (0, 0)
            trace.append({"model": model, "status": "ok", "latency_ms": elapsed})
            return parsed, CallResult(
                content=parsed,
                model=model,
                latency_ms=elapsed,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                trace=trace,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.perf_counter() - started) * 1000)
            trace.append(
                {
                    "model": model,
                    "status": "error",
                    "latency_ms": elapsed,
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                }
            )
            logger.warning("LLM %s failed for task %s: %s", model, task, exc)

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
