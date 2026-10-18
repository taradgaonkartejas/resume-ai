"""Redis response cache for deterministic LLM tasks.

Redis has been in `docker-compose.yml`, `environment.yaml` and `config.py`
since the beginning and was read by no application code at all. This module is
its one job.

Why a cache is the right job for Redis here, when a checkpointer is not: a
cache is *allowed to lose data*, which is exactly what this Redis is
configured to do. LangGraph checkpoints are not allowed to lose data, which is
why they live in Postgres alongside everything else -- one backup, one
restore.

Three rules decide what is cached, and all three are about correctness rather
than hit rate.

1. **Never the writer.** `MODEL_WRITING` runs at temperature 0.4 on purpose,
   and the `critic_rejected` card invites the user to try again -- capped at
   two presses. That retry is only worth pressing because the output varies.
   A cache would silently turn "try again" into "show me that again", which
   is worse than no cache: it would look like the model is stubborn rather
   than cached.

2. **The prompt template is part of the key.** The sharpest edge in the whole
   design: edit a rule in `prompts.py`, and without this you keep serving
   answers produced by the old rule. The key carries a fingerprint of
   `prompts.py` itself, so any edit to any prompt invalidates everything.
   Automatic, because a version constant someone must remember to bump is a
   version constant that will not be bumped.

3. **Never cache a failure.** Not timeouts, not HTTP 402 `quota_exhausted`,
   not a salvage that failed. A cached failure turns a transient provider
   problem into a permanent one for the TTL.

Availability: the cache must never be able to break an analysis. Every entry
point swallows every Redis exception, logs once, and returns a miss. A test
runs the whole suite against an unreachable REDIS_URL.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import threading
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Tasks whose answer is a function of their input. The writer is absent by
# design (rule 1 above); `chat` is absent because a conversation turn is not
# a pure function of its prompt and because `chat_tokens_left` accounting
# should not silently stop decrementing.
CACHEABLE_TASKS = frozenset({"extraction", "jd_analysis", "scoring", "ats"})

TTL_SECONDS = 7 * 24 * 60 * 60

_LOCK = threading.Lock()
_CLIENT: Any = None
_CLIENT_READY = False
_WARNED = False
_PROMPTS_FINGERPRINT: str | None = None


def _prompts_fingerprint() -> str:
    """Hash of `prompts.py`, so editing any prompt invalidates every entry."""
    global _PROMPTS_FINGERPRINT
    if _PROMPTS_FINGERPRINT is None:
        try:
            from app.ai import prompts

            raw = pathlib.Path(prompts.__file__).read_bytes()
            _PROMPTS_FINGERPRINT = hashlib.sha256(raw).hexdigest()[:16]
        except Exception:  # noqa: BLE001 — a cache must not break on this
            # Unknown template state: use a value that cannot collide with a
            # real fingerprint, so nothing written under a known one is served.
            _PROMPTS_FINGERPRINT = "unknown"
    return _PROMPTS_FINGERPRINT


def reset_for_tests() -> None:
    """Drop the memoised client and fingerprint."""
    global _CLIENT, _CLIENT_READY, _WARNED, _PROMPTS_FINGERPRINT
    with _LOCK:
        _CLIENT = None
        _CLIENT_READY = False
        _WARNED = False
        _PROMPTS_FINGERPRINT = None


def enabled() -> bool:
    return bool(getattr(settings, "llm_cache_enabled", False))


def _client() -> Any:
    """Lazily connected Redis client, or None. Never raises."""
    global _CLIENT, _CLIENT_READY, _WARNED
    if _CLIENT_READY:
        return _CLIENT
    with _LOCK:
        if _CLIENT_READY:
            return _CLIENT
        _CLIENT_READY = True
        if not enabled():
            _CLIENT = None
            return None
        try:
            import redis

            client = redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=0.25,
                socket_timeout=0.25,
                decode_responses=True,
            )
            client.ping()
            _CLIENT = client
            logger.info("LLM response cache: connected to %s", settings.redis_url)
        except Exception as exc:  # noqa: BLE001 — degrade to no cache
            if not _WARNED:
                _WARNED = True
                logger.warning(
                    "LLM response cache unavailable (%s: %s) — running without "
                    "it. This is not an error; analysis is unaffected.",
                    type(exc).__name__,
                    exc,
                )
            _CLIENT = None
        return _CLIENT


def cacheable(task: str, temperature: float) -> bool:
    """Both conditions must hold, and they are checked independently.

    The task allowlist is the policy. The temperature check is a backstop: if
    someone later calls a cacheable task at a creative temperature, that call
    is expecting variation and must not be served from a cache.
    """
    return enabled() and task in CACHEABLE_TASKS and temperature <= 0.2


def make_key(
    *,
    task: str,
    model: str,
    system: str,
    user: str,
    temperature: float,
    schema: str = "",
) -> str:
    payload = json.dumps(
        {
            "task": task,
            "model": model,
            "system": system,
            "user": user,
            "temperature": temperature,
            "schema": schema,
            "prompts": _prompts_fingerprint(),
            "v": 1,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return "resumeai:llm:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get(key: str) -> dict | None:
    client = _client()
    if client is None:
        return None
    try:
        blob = client.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.debug("cache read failed: %s", exc)
        return None
    if not blob:
        return None
    try:
        value = json.loads(blob)
    except Exception:  # noqa: BLE001 — a corrupt entry is a miss
        return None
    return value if isinstance(value, dict) else None


def put(key: str, value: dict) -> None:
    client = _client()
    if client is None:
        return
    try:
        # `set(..., ex=)` not `setex`: setex is deprecated in redis-py >= 2.6.
        client.set(key, json.dumps(value, ensure_ascii=False), ex=TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.debug("cache write failed: %s", exc)
