import re
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# app/config.py -> app/ -> backend/ -> repo root
BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = Path(__file__).resolve().parents[2]

# Two anchors, deliberately:
#
#   BACKEND_DIR  where .env lives. The backend owns its own configuration, so
#                the frontend can own frontend/.env.local without the two
#                fighting over one file at the root.
#   ROOT_DIR     where runtime artefacts live (data/). It stays at the repo
#                root because that is where the existing exports, the SQLite
#                fallback database and the LangGraph checkpoints already are.
#                Repointing it at backend/ would silently strand all of them.
#
# Both are absolute and derived from __file__, never from the cwd: env_file is
# resolved relative to the working directory, so a relative ".env" would load
# when uvicorn starts in backend/ and silently not load anywhere else — every
# setting falling back to its default with no error.


# Values that are syntactically present but cannot be a credential. No real
# key starts with "#", and none is a bracketed/angled placeholder.
_PLACEHOLDER_RE = re.compile(r"^(#|<|\[|your[-_ ]|xxx+$|changeme$|todo$)", re.I)


def is_placeholder(value: str) -> bool:
    """True when `value` is blank or obviously not a real API key.

    An unset key is free -- the provider is dropped without a network call. A
    garbage key costs a round trip and returns 401, which reads like an
    entitlement problem rather than the config typo it is.
    """
    text = str(value or "").strip()
    return not text or bool(_PLACEHOLDER_RE.match(text))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # LLM — optional; the app degrades gracefully without a key.
    #
    # One key per provider. A chain spans vendors, so holding only one of
    # these is fine: resolve_chain() skips the models it has no key for.
    # Base URLs live in llm.PROVIDERS, not here -- they are facts about the
    # vendor, not things an operator should be editing.
    nvidia_api_key: str = ""      # build.nvidia.com  — no card, ~40 req/min
    groq_api_key: str = ""        # console.groq.com  — no card, 30 req/min
    unorouter_api_key: str = ""   # legacy; 1 req/min/model, hence the move

    # Per-task routing, qualified as "provider:model".
    #
    # writing/chat go to a chat-tuned model because the user reads that prose.
    # critique goes to a DIFFERENT VENDOR on purpose: the critic exists to
    # catch what the writer got wrong, and a model does not audit itself well.
    #
    # NVIDIA ids below were read from the live catalogue (82 models) on
    # 2026-09-27, not guessed -- an earlier draft of this block invented
    # "kimi-k2.5", "qwen/qwen3.5" and "nemotron-3-super-120b", none of which
    # exist. Free catalogues churn, so re-check with `make models`, which
    # flags any id that is not in the provider's catalogue.
    # Every id below carries a "Free Endpoint" badge on build.nvidia.com or is
    # on Groq's free list. kimi-k2.6 and glm-5.3-flash used to sit here and are
    # NOT free endpoints -- they 404 and time out respectively on this plan.
    model_default: str = "groq:openai/gpt-oss-120b"
    # nemotron-3-super was retired (410) on 2026-10-03 and took five task
    # slots with it. The writer must stay on a DIFFERENT VENDOR from the
    # critic (groq) -- a writer graded by its own family is not an
    # independent measurement, and there is a test that enforces it.
    #
    # UNVERIFIED FOR GENERATION. nemotron-3-ultra-550b-a55b answered the
    # 2026-10-02 probe in 3.22s, but that was a 1-token ping, and this
    # project has already been bitten twice by trusting one: lightning-30b
    # pings in 0.33s and times out at 45s on real work.
    #
    # Run `make models-gen-all PROVIDER=nvidia` to measure every free NVIDIA
    # model on a real generation, then set this to the fastest usable one.
    # If this id is slow or dead, the chain falls through to Groq, so the
    # app degrades rather than breaks.
    model_writing: str = "nvidia:nvidia/nemotron-3-ultra-550b-a55b"
    model_chat: str = "groq:openai/gpt-oss-120b"
    model_critique: str = "groq:openai/gpt-oss-120b"
    model_extraction: str = "groq:openai/gpt-oss-120b"
    # NOT nemotron-3.5-lightning-30b-a3b. It pings in 0.33s -- the fastest
    # model in the catalogue -- and then times out at 45s on a real 40-word
    # generation. Chosen on ping latency one turn after building the tool
    # whose entire purpose is that a ping does not predict generation.
    model_jd_analysis: str = "groq:openai/gpt-oss-120b"
    model_scoring: str = "groq:openai/gpt-oss-20b"
    # The ATS agent is NOT scoring work and must not inherit the 20b model.
    # Scoring writes prose about a number that is already decided -- low
    # stakes, so the small model is right. The ATS agent makes the hardest
    # judgement in the product: whether "My Journey" is a hidden work history
    # (relabel) or "Leadership & Achievements" is genuinely custom (leave
    # alone). Those are structurally identical and only the content tells
    # them apart. It was briefly wired to `scoring`, which silently put the
    # smallest model in the catalogue on the one task where being wrong
    # makes the user hand-edit a document that was already correct.
    model_ats: str = "groq:openai/gpt-oss-120b"

    # Unused while embeddings_remote is False (see below) but kept valid so
    # a flip of that flag does not 404.
    embedding_model: str = "nvidia:nvidia/nemotron-3-embed-1b"
    # When true, a model that is not free on the plan we are on is refused
    # before a request is ever sent. A fallback chain is exactly the code path
    # that quietly reaches for a paid model at 3am. Opt out deliberately.
    free_models_only: bool = True
    # Embeddings run locally by default, and not only to save money.
    #   1. gemini-embedding-2:free reports 24.3% success, so ~3 in 4 calls
    #      already fell through to the local embedder.
    #   2. The pgvector column is Vector(embedding_dim) = 384. Hosted Gemini
    #      embeddings are not 384-dimensional, so a *successful* remote call is
    #      the case that breaks the insert.
    #   3. Mixing two embedding spaces in one index makes cosine similarity
    #      meaningless -- worse than either space used consistently.
    # For a 5-user local deployment over a small corpus, one consistent space
    # beats a 24% chance of a better one.
    embeddings_remote: bool = False
    llm_timeout_seconds: int = 45
    critic_max_revisions: int = 2

    # Data
    database_url: str = "postgresql+psycopg2://admin:Admin123@localhost:5433/resumeai"
    allow_sqlite_fallback: bool = True
    redis_url: str = "redis://localhost:6379/0"

    # Object storage
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minio"
    s3_secret_key: str = "minio123"
    s3_bucket_uploads: str = "resumeai-uploads"
    s3_bucket_exports: str = "resumeai-exports"
    allow_disk_fallback: bool = True

    # App
    seed_user_count: int = 5
    chat_token_allowance: int = 25
    max_upload_mb: int = 10
    embedding_dim: int = 384

    @property
    def llm_configured(self) -> bool:
        """Any provider key at all. See llm.is_configured() for the live check.

        Uses is_placeholder() rather than truthiness so a leftover comment or
        `<your-key>` does not read as a configured provider here while
        llm.Provider.configured correctly rejects it.
        """
        return any(
            not is_placeholder(k)
            for k in (self.nvidia_api_key, self.groq_api_key, self.unorouter_api_key)
        )


settings = Settings()
