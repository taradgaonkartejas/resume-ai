"""Embeddings with a deterministic local fallback.

The provider's embedding model reports ~74% success, so the fallback is not
optional. The local embedding is a hashed bag-of-words: no semantic
generalisation, only lexical overlap — but it is deterministic, offline and
good enough to rank bullets.
"""

from __future__ import annotations

import hashlib
import logging
import math

from app.ai import llm
from app.config import settings

logger = logging.getLogger(__name__)


def local_embed(text: str, dim: int | None = None) -> list[float]:
    """Deterministic hashed embedding. Never fails, never calls the network."""
    dim = dim or settings.embedding_dim
    vec = [0.0] * dim
    for token in _tokenise(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        vec[int.from_bytes(digest[:4], "big") % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm:
        vec = [v / norm for v in vec]
    return vec


def _tokenise(text: str) -> list[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return [t for t in cleaned.split() if t]


def embed(text: str) -> list[float]:
    """Remote when configured, local otherwise or on failure."""
    if llm.is_configured():
        try:
            return llm.embed_remote([text])[0]
        except Exception as exc:  # noqa: BLE001 — degrade, never fail a request
            logger.warning("Remote embedding failed, using local: %s", exc)
    return local_embed(text)


def embed_many(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if llm.is_configured():
        try:
            return llm.embed_remote(texts)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Remote batch embedding failed, using local: %s", exc)
    return [local_embed(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
