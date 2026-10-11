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
from app.config import is_placeholder, settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# --------------------------------------------------------------------------
# Providers
#
# One process now talks to several vendors. Model ids are qualified as
# "provider:model" so a fallback chain can cross company boundaries -- a 429
# from one vendor degrades to a different vendor's hardware rather than to a
# sibling model sitting behind the same rate limiter.
#
# The deeper reason is correctness, not quota. DESIGN.md justifies the critic
# as "a second agent that only judges truthfulness". Running it on the same
# model as the writer makes that independence notional: same network, same
# blind spots, grading its own homework. Different families make it real.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Provider:
    """An OpenAI-compatible endpoint and what it will accept."""

    name: str
    base_url: str
    key_setting: str
    # reasoning_effort vocabularies differ: OpenAI/Gemini accept "minimal",
    # Groq's gpt-oss accepts low/medium/high. Sending the wrong word is a 400.
    efforts: frozenset[str] = frozenset()
    free_models: frozenset[str] = frozenset()
    # True when the provider's free plan covers its whole catalogue and no card
    # is on file, so there is no paid model to accidentally reach for.
    free_covers_catalogue: bool = False

    @property
    def api_key(self) -> str:
        return str(getattr(settings, self.key_setting, "") or "")

    @property
    def configured(self) -> bool:
        """True only for a value that could plausibly be a key.

        An unset key is free: resolve_chain drops the provider without a
        network call. A *garbage* key costs a round trip and returns 401,
        which reads like an entitlement problem rather than a config typo.

        This mattered in practice. `.env.example` carried its documentation as
        a trailing comment on the assignment line:

            CEREBRAS_API_KEY=     # cloud.cerebras.ai  1M tokens/day, ...

        Copied to `.env` and left unfilled, the comment itself was loaded as
        the key, and `make models` reported a 401 for a provider the user had
        deliberately not configured.
        """
        return not is_placeholder(self.api_key)


PROVIDERS: dict[str, Provider] = {
    # No credit card, no daily token cap, ~40 req/min, and a catalogue of
    # frontier-class open models. Verify reachability with `make models`.
    "nvidia": Provider(
        name="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        key_setting="nvidia_api_key",
        efforts=frozenset({"low", "medium", "high"}),
        # NOT free_covers_catalogue. build.nvidia.com lists ~81 models but
        # only a subset carry a "Free Endpoint" badge; the rest are listed for
        # download or for accounts with credits. Calling an unbadged one
        # returns 404 "Function '<uuid>': Not found for account '<id>'",
        # which reads like a broken model id rather than a billing boundary.
        #
        # Declaring the catalogue free was the root cause of the kimi-k2.6
        # incident: free_models_only is ON by default and exists precisely to
        # refuse a non-free model before a request is sent, and it waved the
        # model through because this flag said everything here was free.
        #
        # Maintained by hand from the "Free Endpoint" filter on
        # build.nvidia.com/models, because the /v1/models response does not
        # expose the badge. `make models-probe` regenerates it empirically --
        # on a credit-less account, "answers" and "has a free endpoint" are
        # the same thing.
        # MEASURED by `make models-probe` on 2026-10-02, not transcribed from
        # the "Free Endpoint" badges on build.nvidia.com. That distinction is
        # not pedantic: of 12 ids read off the badge list by eye, three were
        # wrong (gemma-4-31b-it is badged but times out;
        # nemotron-3-nano-omni-30b-a3b-reasoning errors;
        # "ising-calibration-1-35b-a3b" was a misreading of
        # "ising-calibration-1.5-31b") and five callable models were missed.
        #
        # Badged, listed and callable are three different things. Only the
        # last one matters, and only a real call establishes it.
        #
        # 12 of 65 advertised chat models answer on a credit-less account.
        # Regenerate with: make models-probe
        free_models=frozenset({
            "google/diffusiongemma-26b-a4b-it",
            "meta/llama-3.2-11b-vision-instruct",
            "meta/llama-3.2-90b-vision-instruct",
            "meta/muse-glimmer-30b",
            "nvidia/ising-calibration-1.5-31b",
            # nemotron-3-super-120b-a12b was here and was the writer. NVIDIA
            # retired it at 2026-10-03T09:00:00Z (HTTP 410), ~9h after the
            # probe below recorded it at 0.45s. A measured allowlist is a
            # snapshot, not a standing fact -- re-run `make models-probe`.
            "nvidia/nemotron-3-ultra-550b-a55b",
            "nvidia/nemotron-3.5-content-safety",
            # The probe emits CHAT models only -- it filters anything it
            # cannot exercise through /chat/completions, which includes
            # embeddings. Pasting its output verbatim therefore dropped the
            # embedding model from the allowlist, and free_models_only then
            # refused the model the vector store depends on. Caught by
            # test_the_embedding_model_is_free.
            "nvidia/nemotron-3-embed-1b",
            "nvidia/nemotron-3.5-lightning-30b-a3b",
            "nvidia/nemotron-parse-2.0",
            "openai/gpt-oss-20b",
            "poolside/laguna-xs-2.1",
        }),
    ),
    # 30 req/min but 8,000 tokens/min, which binds first at our ~714 tokens per
    # call -- so this is roughly 11 effective req/min. Used for the critic,
    # deliberately a different family from the writer.
    "groq": Provider(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        key_setting="groq_api_key",
        efforts=frozenset({"low", "medium", "high"}),
        free_models=frozenset(
            {
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
                "openai/gpt-oss-safeguard-20b",
            }
        ),
    ),
    # 1M tokens/month, 60 req/min, no card, whole catalogue on the free plan.
    # That rate limit is 60x Groq's effective ~11 req/min and is the reason
    # this is here at all: `ats-report-llm` was being 429'd mid-run.
    #
    # ADDED ON THE USER'S EXPLICIT INSTRUCTION, over a recorded objection.
    # Three things about this provider are unresolved and a future reader
    # should not mistake its presence for an endorsement:
    #
    #   1. NO json_schema. Their docs list six capabilities and `json_mode`
    #      is the structured one: `response_format: {"type":"json_object"}`.
    #      Our structured() asks for method="json_schema". If they 400 on it
    #      the call dies; if they accept-and-ignore it, the `ok-json` salvage
    #      rung may recover. Unmeasured -- run `make models-gen-all codecraft`,
    #      which now probes a real schema, before trusting this for writing.
    #   2. No upstream provider is named anywhere on the site, and the model
    #      catalogue is internally inconsistent: `muse-spark-1.1` is described
    #      as a Meta series while carrying Zhipu's logo, and names like
    #      `gpt-5.6-terra` and `claude-mythos-preview` are not products those
    #      vendors ship. Writer and extraction payloads are the user's real
    #      name, phone, address and employment history.
    #   3. Because (2) means we cannot tell which upstream model answers,
    #      two different ids here may be one model behind two labels -- which
    #      is why the critic stays on a named vendor. See the independence
    #      rule at the top of this file.
    #
    # free_covers_catalogue: their pricing page states every plan, free
    # included, reaches all models; tiers differ only in volume and rate.
    "codecraft": Provider(
        name="codecraft",
        base_url="https://codecraftapi.com/v1",
        key_setting="codecraft_api_key",
        # Unverified vocabulary. Their docs describe `reasoning` as a model
        # capability but document no reasoning_effort parameter, so send
        # none rather than risk a 400 on every call.
        efforts=frozenset(),
        free_covers_catalogue=True,
    ),
    # Retained so an existing .env keeps working. Its free tier allows one
    # request per minute per model per account, which is why we moved.
    "unorouter": Provider(
        name="unorouter",
        base_url="https://api.unorouter.com/v1",
        key_setting="unorouter_api_key",
        efforts=frozenset({"minimal", "low", "medium", "high"}),
    ),
}

DEFAULT_PROVIDER = "nvidia"


def split_model(qualified: str) -> tuple[str, str]:
    """"provider:model" -> ("provider", "model").

    Splits on the FIRST colon only, because UnoRouter model ids carry a
    ":free" suffix of their own. An unqualified id is assumed to belong to
    DEFAULT_PROVIDER so older configs keep working.
    """
    text = (qualified or "").strip()
    head, sep, tail = text.partition(":")
    if sep and head in PROVIDERS:
        return head, tail
    # Unqualified. A ":free" suffix is UnoRouter's own naming convention, so a
    # legacy id routes back to UnoRouter rather than being handed to a vendor
    # that has never heard of it -- which is exactly how you get a 403
    # "this token has no access to model X" that looks like an entitlement
    # problem and is really a routing bug.
    if text.endswith(":free"):
        return "unorouter", text
    return DEFAULT_PROVIDER, text


def provider_for(qualified: str) -> Provider:
    return PROVIDERS[split_model(qualified)[0]]


# Per-task routing (DESIGN.md §6.4). The writer and the critic are on
# different vendors on purpose; see the module header.
TASK_MODELS: dict[str, str] = {
    "extraction": settings.model_extraction,
    "jd_analysis": settings.model_jd_analysis,
    "scoring": settings.model_scoring,
    "ats": settings.model_ats,
    "writing": settings.model_writing,
    "critique": settings.model_critique,
    "chat": settings.model_chat,
}

# Ordered so that consecutive entries sit on *different* providers wherever
# possible: the most common reason to need a fallback is a per-provider rate
# limit, and another model behind the same limiter is no help.
FALLBACK_CHAIN: list[str] = [
    settings.model_default,
    "groq:openai/gpt-oss-120b",
    # nvidia:z-ai/glm-5.3-flash sat here and was removed: `make models` showed
    # it burning the full 45s timeout rather than answering. A model that times
    # out inside the SHARED chain is not one task's problem -- Groq's 8k
    # tokens/min cap means falling through is routine, so every task that got
    # this far paid 45 seconds before moving on.
    #
    # It was also the chain's only NVIDIA entry, so dropping it alone left the
    # fallback Groq-only: one vendor's rate limit would take out every tier at
    # once, which is the exact failure this chain exists to survive. Nemotron
    # replaces it because it is the one NVIDIA model OBSERVED to answer on
    # this account, not because it looked plausible.
    #
    # 2026-10-03: that replacement, nemotron-3-super-120b-a12b, was itself
    # RETIRED by NVIDIA (HTTP 410, end of life 09:00Z) about nine hours after
    # a probe measured it at 0.45s. I deleted the dead id and left this very
    # comment standing -- reinstating, by hand, the Groq-only chain it warns
    # against. A test caught it. Twice now, the test and not me.
    #
    # ultra-550b-a55b answered the 2026-10-02 probe and is UNVERIFIED for
    # real generation: run `make models-gen-all PROVIDER=nvidia`.
    #
    # 2026-10-11: VERIFIED, and it fails. Production log, task=scoring:
    #
    #   LLM nvidia:nvidia/nemotron-3-ultra-550b-a55b failed for task scoring
    #   (timeout): Request timed out.          latency_ms: 45187
    #
    # Third model in a row to ping fast and time out on real work, and the
    # prediction was sitting in this comment the whole time. It is doubly
    # expensive here: it is MODEL_WRITING *and* the chain's only NVIDIA
    # entry, so the writer pays 45s directly and every other task pays 45s
    # on fall-through. Until `models-gen-all` names a replacement that is
    # OBSERVED to generate, assume this slot is dead weight.
    "nvidia:nvidia/nemotron-3-ultra-550b-a55b",
    "groq:openai/gpt-oss-20b",
]


# Per-task reasoning effort. The critic compares two short strings and returns
# {approved, notes, severity}; it does not need deep thinking. The *word* used
# differs by vendor, so this table stores intent and effort_for() maps it onto
# whatever the chosen provider actually accepts.
TASK_EFFORT: dict[str, str] = {
    "critique": "minimal",
    # Writing summaries measured 27.6-42.1s against a 45s budget, versus
    # 2.9-10.1s for bullets -- a 6x gap on the same model with the same RAG
    # context. At ~17 output tokens per visible word, most of that is
    # reasoning, so effort is the lever rather than the timeout.
    #
    # Rewriting one sentence to fix one stated problem is not a reasoning
    # task; it is the same argument already applied to the critic above. The
    # risk is that prose quality drops, which is exactly what the writer
    # corpus measures -- compare fix rate AND the `sec` column before and
    # after, and revert if the fix rate falls.
    "writing": "low",
}

# "minimal" does not exist on Groq/NVIDIA; "low" is the nearest thing.
_EFFORT_ALIASES: dict[str, str] = {"minimal": "low"}


def effort_for(task: str, model: str | None = None) -> str | None:
    """Reasoning effort for a task on a given model, or None to omit the field.

    Returning None means the parameter is absent from the request body, not
    sent as null -- that keeps payloads byte-identical for tasks we have not
    tuned.
    """
    wanted = TASK_EFFORT.get(task)
    if wanted is None:
        return None
    if model is None:
        return wanted
    accepted = provider_for(model).efforts
    if not accepted or wanted in accepted:
        return wanted
    alias = _EFFORT_ALIASES.get(wanted)
    return alias if alias in accepted else None


def is_free(model: str) -> bool:
    """Is this model free on the plan we are actually on?

    Previously this tested `model.endswith(":free")`, which was UnoRouter's
    naming convention rather than a fact about billing. No Groq or NVIDIA
    model is named that way, so the old test would have refused every model on
    the new providers and resolved every chain to empty -- every call would
    have raised before it was sent.

    Freeness is a property of the account and plan, not of the model's name,
    so it is declared per provider.
    """
    text = (model or "").strip()
    head, sep, _ = text.partition(":")
    explicitly_qualified = bool(sep and head in PROVIDERS)

    name, bare = split_model(text)
    provider = PROVIDERS.get(name)
    if provider is None:
        return False
    if bare.endswith(":free"):
        return True  # UnoRouter grammar, still honoured
    if not explicitly_qualified:
        # An unqualified id cannot be attributed to a vendor with confidence,
        # so we will not vouch for its price. Without this, a bare paid model
        # name would inherit the default provider's catalogue-wide free tier
        # and sail through the guard.
        return False
    if provider.free_covers_catalogue:
        return True
    return bare in provider.free_models


def _no_key_message() -> str:
    """Name the providers that are actually missing a key.

    This used to read "No UNOROUTER_API_KEY configured" no matter which
    provider was absent -- a leftover from the single-provider era that would
    have sent anyone debugging it to the wrong dashboard.
    """
    names = ", ".join(
        f"{p.key_setting.upper()}" for p in PROVIDERS.values() if not p.configured
    )
    return f"No provider API key configured (unset or placeholder: {names})"


# Logged once per model per process. This fired on every call and buried the
# report it was printed inside.
_DROP_LOGGED: set[str] = set()


def resolve_chain(task: str) -> list[str]:
    """The models to try for a task, nearest first.

    Filters out (a) paid models when settings.free_models_only is set, and
    (b) providers we hold no key for. (b) matters now that a chain spans
    vendors: holding only an NVIDIA key should silently skip the Groq entries
    rather than spend a network round trip discovering it.
    """
    preferred = TASK_MODELS.get(task, settings.model_default)
    # dict.fromkeys, not a != filter: that only removed the PREFERRED model,
    # so when model_default equalled a later chain entry the same id appeared
    # twice and the chain burned a second identical attempt on a model that
    # had just failed. Visible in a live run as
    # "... -> groq:openai/gpt-oss-120b -> groq:openai/gpt-oss-120b -> ...".
    chain = list(dict.fromkeys([preferred, *FALLBACK_CHAIN]))

    usable = [m for m in chain if provider_for(m).configured]
    skipped = [m for m in chain if not provider_for(m).configured]
    if skipped:
        logger.debug("no key for provider(s); skipping: %s", ", ".join(skipped))
    chain = usable

    if not settings.free_models_only:
        if not chain:
            raise LLMUnavailable(
                f"No provider key is configured for any model in the {task} chain"
            )
        return chain

    free = [m for m in chain if is_free(m)]
    dropped = [m for m in chain if not is_free(m)]
    if dropped:
        # "paid" was a guess dressed as a fact. A dropped id is simply not in
        # the measured free allowlist, and the likeliest reason now is that it
        # was RETIRED -- nemotron-3-super went 410 mid-project and this line
        # then called it "paid" on every single call. Say what is known.
        for model in dropped:
            if model not in _DROP_LOGGED:
                _DROP_LOGGED.add(model)
                logger.warning(
                    "%s is not in the measured free allowlist for its provider "
                    "(retired, never free, or the allowlist is stale) -- "
                    "skipping it. Re-run `make models-probe`.",
                    model,
                )
    if not free:
        raise LLMUnavailable(
            "free_models_only is on but no free, key-configured model is "
            f"available for {task} (chain was: {', '.join(chain)}). On NVIDIA, "
            "free means the model carries a 'Free Endpoint' badge on "
            "build.nvidia.com/models -- most of the ~81 listed models do not, "
            "and calling one returns 404 'Function not found for account'. "
            "Pick a badged id, run `make models-probe` to see what answers, or "
            "set FREE_MODELS_ONLY=false if you have credits."
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

    # 410 Gone is permanent and is a CONFIG fact, not a runtime blip. Free
    # endpoints are retired on a date, sometimes mid-project: this project's
    # writer died at 2026-10-03T09:00:00Z, nine hours after a probe recorded
    # it as the fastest usable model. Retrying cannot help; the id must
    # change. Bucketing it as a plain "error" hides that.
    if status == 410 or "end of life" in text.lower() or "'status': 410" in text:
        return "retired"
    # 402 is a MONTHLY ALLOWANCE, not a per-minute throttle, and the
    # difference decides what to do about it. A 429 clears by waiting a few
    # seconds; a 402 does not clear until the calendar does. codecraft's free
    # plan is 1M tokens/month and returns 402 once that is spent with no
    # balance on file.
    #
    # Bucketing it as a plain "error" would make an exhausted budget look
    # exactly like a bad model: run `writer-report-llm` on the 28th of a
    # heavy month and every codecraft call fails identically to a model that
    # cannot follow instructions. That is the precise confusion this function
    # exists to prevent -- see the docstring.
    if status == 402 or "payment required" in text.lower() or "insufficient" in text.lower():
        return "quota_exhausted"
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
    """True when at least one provider has a key.

    Deliberately not "the default provider has a key": a chain spanning
    vendors is usable with only one of them configured, and resolve_chain()
    drops the rest.
    """
    return any(p.configured for p in PROVIDERS.values())


def _client(model: str, temperature: float = 0.2, reasoning_effort: str | None = None):
    from langchain_openai import ChatOpenAI

    # The endpoint and key come from the model's own provider, so one process
    # can hold several vendors at once.
    provider, bare = split_model(model)
    endpoint = PROVIDERS[provider]

    # reasoning_effort is a named field on ChatOpenAI (str | None), so it must
    # be passed explicitly -- routing it through model_kwargs raises a conflict.
    # None omits it from the request body entirely.
    return ChatOpenAI(
        model=bare,
        base_url=endpoint.base_url,
        api_key=endpoint.api_key or "missing-key",
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
        raise LLMUnavailable(_no_key_message())

    from langchain_core.messages import HumanMessage, SystemMessage

    chain = resolve_chain(task)
    trace: list[dict] = []

    for model in chain:
        started = time.perf_counter()
        try:
            response = _client(model, temperature, effort_for(task, model)).invoke(
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
        raise LLMUnavailable(_no_key_message())

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
                    model, temperature, effort_for(task, model)
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
        raise LLMUnavailable(_no_key_message())

    from langchain_openai import OpenAIEmbeddings

    provider, bare = split_model(settings.embedding_model)
    endpoint = PROVIDERS[provider]
    client = OpenAIEmbeddings(
        model=bare,
        base_url=endpoint.base_url,
        api_key=endpoint.api_key or "missing-key",
        timeout=settings.llm_timeout_seconds,
    )
    return client.embed_documents(texts)
