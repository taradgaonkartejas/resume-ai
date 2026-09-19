from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    try:
        eng = create_engine(settings.database_url, pool_pre_ping=True)
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        return eng, "postgresql"
    except Exception:
        if not settings.allow_sqlite_fallback:
            raise
        # Degraded mode: no pgvector, retrieval falls back to numpy cosine.
        return create_engine(
            "sqlite:///./data/resumeai.db",
            connect_args={"check_same_thread": False},
        ), "sqlite"


engine, DB_BACKEND = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
HAS_PGVECTOR = False


def init_db() -> None:
    global HAS_PGVECTOR
    if DB_BACKEND == "postgresql":
        with engine.begin() as c:
            c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            HAS_PGVECTOR = bool(
                c.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname='vector'")
                ).scalar()
            )
    from app import models  # noqa: F401  — register tables before create_all
    Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()