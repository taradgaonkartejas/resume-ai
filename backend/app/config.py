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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # LLM — optional; the app degrades gracefully without a key
    unorouter_api_key: str = ""
    unorouter_base_url: str = "https://api.unorouter.com/v1"
    model_default: str = "gemini-3.5-flash-lite:free"
    embedding_model: str = "gemini-embedding-2:free"
    # When true, any model whose name does not end in ":free" is refused before
    # a request is ever sent. UnoRouter bills per token, the default model does
    # not have 100% uptime, and a fallback chain is exactly the code path that
    # quietly reaches for a paid model at 3am. Opt out deliberately.
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
        return bool(self.unorouter_api_key)


settings = Settings()
