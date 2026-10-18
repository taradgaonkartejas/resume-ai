"""Embeddings: a local semantic model, with a deterministic fallback.

Three backends, chosen by `settings.embedding_backend`:

    minilm  (default)  all-MiniLM-L6-v2 via fastembed/ONNX, local, semantic
    hashed             deterministic hashed bag-of-words, local, lexical
    remote             a hosted embedding model (still off; see below)

**Why this changed.** The hashed embedder was the default and was never a
weak semantic model -- it was not a semantic model at all. Measured on real
resume text:

    "Deployed microservices on Kubernetes" / "Shipped services to K8s"  0.000
    "Experience with CI/CD pipelines" / "Built Jenkins and GH Actions"  0.000
    "Increased conversion by 30%"    / "Decreased conversion by 30%"    0.750
    "...managing the team"           / "...managing the budget"         0.800

It scored an exact negation at 0.750 and shared boilerplate at 0.800 while
scoring true synonyms at 0.000 -- it ranked filler above meaning, and the job
it is used for is matching JD requirements to resume bullets, which is
exactly where synonyms live.

**Why local and not hosted.** all-MiniLM-L6-v2 emits **384** dimensions, the
width the pgvector column already had. So the semantic upgrade needs no
schema change, no network call and no privacy exposure. That last point is
the decisive one: an embedding payload is whole resume text -- name, phone,
employment history -- which is the same data that excluded hosted Gemini.
`embeddings_remote` stays False.

**Why `hashed` survives.** It never fails and never touches the network,
which keeps the test suite fast and offline, and it is the fallback when the
model file is absent. That fallback logs loudly: silently degrading from
semantic to lexical would look like a quality regression with no cause.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading

from app.config import settings

logger = logging.getLogger(__name__)

MINILM_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

_MODEL_LOCK = threading.Lock()
_MODEL = None
_MODEL_TRIED = False
_FALLBACK_WARNED = False


# ----------------------------------------------------------------- backends
def active_backend() -> str:
    """The backend that will actually be used, after availability checks.

    Recorded on every row so a search can refuse to compare vectors from two
    different spaces.
    """
    want = getattr(settings, "embedding_backend", "minilm")
    if want == "minilm" and _minilm() is None:
        return "hashed"
    return want


def _minilm():
    """Memoised fastembed model, or None if unavailable. Never raises."""
    global _MODEL, _MODEL_TRIED, _FALLBACK_WARNED
    if _MODEL_TRIED:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL_TRIED:
            return _MODEL
        _MODEL_TRIED = True
        try:
            from fastembed import TextEmbedding

            _MODEL = TextEmbedding(model_name=MINILM_MODEL)
            logger.info("Embeddings: %s loaded (local, 384-dim)", MINILM_MODEL)
        except Exception as exc:  # noqa: BLE001 — degrade, never fail a request
            _MODEL = None
            if not _FALLBACK_WARNED:
                _FALLBACK_WARNED = True
                logger.warning(
                    "Embeddings: %s unavailable (%s: %s). Falling back to the "
                    "HASHED embedder, which has no semantic generalisation -- "
                    "retrieval quality will drop. Run `make fetch-models`.",
                    MINILM_MODEL, type(exc).__name__, exc,
                )
        return _MODEL


def reset_for_tests() -> None:
    global _MODEL, _MODEL_TRIED, _FALLBACK_WARNED
    with _MODEL_LOCK:
        _MODEL = None
        _MODEL_TRIED = False
        _FALLBACK_WARNED = False


# ------------------------------------------------------------------- hashed
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


# -------------------------------------------------------------- dimensions
def _checked(vectors: list[list[float]], source: str) -> list[list[float]]:
    """Every vector must be exactly `embedding_dim` wide.

    Both backends emit 384 today, which is precisely how a mismatch ships
    unnoticed later. The pgvector column is fixed-width: a vector of the
    wrong size fails at INSERT with a message that names neither the backend
    nor the expected width.
    """
    want = settings.embedding_dim
    for vector in vectors:
        if len(vector) != want:
            raise ValueError(
                f"{source} produced a {len(vector)}-dimensional vector but "
                f"embedding_dim is {want}; the pgvector column is fixed-width. "
                f"Either set EMBEDDING_DIM={len(vector)} and re-embed, or use "
                f"a {want}-dimensional model."
            )
    return vectors


# --------------------------------------------------------------- public API
def embed(text: str) -> list[float]:
    return embed_many([text])[0]


def embed_many(texts: list[str]) -> list[list[float]]:
    """Batched by design: one model call per resume, not one per bullet."""
    if not texts:
        return []

    backend = getattr(settings, "embedding_backend", "minilm")

    if backend == "remote" and settings.embeddings_remote:
        # Imported HERE, not at module scope. `app.ai.llm` pulls in the whole
        # provider stack, and this module is imported by `app.fetch_models` --
        # a setup script, run precisely when the environment may be broken.
        # Dragging the gateway in to read one constant made the friendly
        # "fastembed is not installed" message unreachable behind a
        # ModuleNotFoundError traceback.
        from app.ai import llm

        if not llm.is_configured():
            return _checked([local_embed(t) for t in texts], "hashed embedder")
        try:
            return _checked(llm.embed_remote(texts), "remote embedder")
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 — degrade, never fail a request
            logger.warning("Remote embedding failed, using local: %s", exc)

    if backend == "minilm":
        model = _minilm()
        if model is not None:
            try:
                vectors = [list(map(float, v)) for v in model.embed(texts)]
                return _checked(vectors, MINILM_MODEL)
            except ValueError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("MiniLM embedding failed, using hashed: %s", exc)

    return _checked([local_embed(t) for t in texts], "hashed embedder")


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
