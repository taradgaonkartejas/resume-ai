"""Pre-fetch the local embedding model.

A local-only app must not download 90 MB silently, mid-analysis, the first
time someone clicks Analyze. This makes the download an explicit setup step
with visible progress, so the failure mode is "setup did not run" rather than
"the first analysis took 40 seconds for no stated reason".

    make fetch-models          /  .\\make.ps1 fetch-models
    python -m app.fetch_models

IMPORT DISCIPLINE MATTERS HERE, and the first version got it wrong. This is
the script you run when your environment is broken -- wrong conda env, deps
not updated -- so every import it makes at module scope is a chance to fail
with a traceback instead of advice. The original imported
`app.ai.embeddings`, which imported `app.ai.llm`, which imported the whole
provider stack and `pydantic_settings`, all to read one string constant. The
friendly "fastembed is not installed" message below was unreachable: the
chain exploded before main() ran.

Everything is therefore imported inside main(), in order of how likely it is
to be the thing that is wrong, and each failure prints a fix.
"""

from __future__ import annotations

import sys

# Duplicated deliberately so this file imports nothing to find it out.
# `test_fetch_models.py` asserts it equals app.ai.embeddings.MINILM_MODEL, so
# the two cannot drift apart silently.
MINILM_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def main() -> int:
    # 1. The dependency, first: the most likely thing to be missing, and the
    #    one check that needs nothing from this application at all.
    try:
        from fastembed import TextEmbedding
    except ImportError:
        return _fail(
            "fastembed is not installed.\n"
            "\n"
            "    conda env update -f backend/environment.yaml --prune\n"
            "\n"
            "If that succeeded, you are probably in the wrong environment:\n"
            "    .\\make.ps1 which-python"
        )

    # 2. Settings, second: needs pydantic_settings, which needs the env to be
    #    built. Only reached once the heavier dependency is known good.
    try:
        from app.config import settings
    except ImportError as exc:
        return _fail(
            f"Could not load app.config ({exc}).\n"
            "Run this from the backend/ directory with the conda env active:\n"
            "    conda activate resumeai && cd backend"
        )

    backend = getattr(settings, "embedding_backend", "minilm")
    if backend != "minilm":
        print(
            f"EMBEDDING_BACKEND is {backend!r}, so there is nothing to fetch.\n"
            f"Set it to 'minilm' in backend/.env to use local semantic search."
        )
        return 0

    print(f"Fetching {MINILM_MODEL} (~90 MB, once) ...")
    try:
        model = TextEmbedding(model_name=MINILM_MODEL)
        vector = next(iter(model.embed(["warm up"])))
    except Exception as exc:  # noqa: BLE001 — this is a user-facing setup step
        return _fail(
            f"Fetch failed: {type(exc).__name__}: {exc}\n"
            "\n"
            "This step needs network access once. After it succeeds the model "
            "is cached locally and analysis never touches the network."
        )

    dim = len(vector)
    want = settings.embedding_dim
    if dim != want:
        return _fail(
            f"The model emits {dim} dimensions but EMBEDDING_DIM is {want}.\n"
            f"The pgvector column is fixed-width, so every insert would fail.\n"
            f"Either set EMBEDDING_DIM={dim} and re-embed, or choose a "
            f"{want}-dimensional model."
        )

    print(f"Ready: {dim}-dimensional, cached locally. Analysis makes no "
          f"network call for embeddings.")
    print()
    print("If you just switched EMBEDDING_BACKEND, re-embed now -- stored "
          "vectors from the old backend are not comparable:")
    print("    python -m app.cli db reset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
