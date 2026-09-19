from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# app/config.py -> app/ -> backend/ -> repo root
ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # LLM — optional; the app degrades gracefully without a key
    unorouter_api_key: str = ""
    unorouter_base_url: str = "https://api.unorouter.com/v1"
    model_default: str = "gemini-3.5-flash-lite:free"
    embedding_model: str = "gemini-embedding-2:free"
    llm_timeout_seconds: int = 90          # this model averages ~10s; be generous
    critic_max_revisions: int = 2

    # Data
    database_url: str = "postgresql+psycopg2://resumeai:resumeai@localhost:5433/resumeai"
    allow_sqlite_fallback: bool = True
    redis_url: str = "redis://localhost:6379/0"

    # Object storage
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "resumeai"
    s3_secret_key: str = "resumeai123"
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