
import logging

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import ROOT_DIR, settings

logger = logging.getLogger(__name__)

DATA_DIR = ROOT_DIR / "data"


class Base(DeclarativeBase):
    pass


def _sqlite_url() -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(DATA_DIR / 'resumeai.db').as_posix()}"


def _make_engine():
    try:
        eng = create_engine(settings.database_url, pool_pre_ping=True)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return eng, "postgresql"
    except Exception as exc:
        if not settings.allow_sqlite_fallback:
            raise
        # Falling back silently is how you end up debugging the wrong database.
        # Say so loudly, and say why.
        logger.warning(
            "Postgres at %s is unreachable (%s: %s) — falling back to SQLite. "
            "Set ALLOW_SQLITE_FALLBACK=false to make this a hard failure.",
            _safe_url(settings.database_url),
            type(exc).__name__,
            str(exc).strip().splitlines()[0] if str(exc).strip() else "no detail",
        )
        return (
            create_engine(_sqlite_url(), connect_args={"check_same_thread": False}),
            "sqlite",
        )


def _safe_url(url: str) -> str:
    """Render a DSN without its password."""
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:  # noqa: BLE001
        return "<unparseable url>"


engine, DB_BACKEND = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _detect_pgvector() -> bool:
    """Is the vector extension already installed on this database?

    Detected at import time so that any process — a worker, a script, a test —
    knows the truth without having to call init_db() first. Reading a stale
    False here silently downgrades every search to the Python path.
    """
    if DB_BACKEND != "postgresql":
        return False
    try:
        with engine.connect() as conn:
            return bool(
                conn.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname='vector'")
                ).scalar()
            )
    except Exception:  # noqa: BLE001
        return False


HAS_PGVECTOR = _detect_pgvector()


def has_pgvector() -> bool:
    """Read the flag dynamically.

    `from app.db import HAS_PGVECTOR` captures the value at import time, which
    goes stale the moment init_db() creates the extension. Call this instead.
    """
    return HAS_PGVECTOR


if DB_BACKEND == "sqlite":

    @event.listens_for(engine, "connect")
    def _fk_pragma(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def init_db() -> None:
    """Create the vector extension (Postgres) and all tables."""
    global HAS_PGVECTOR
    if DB_BACKEND == "postgresql":
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                HAS_PGVECTOR = bool(
                    conn.execute(
                        text("SELECT 1 FROM pg_extension WHERE extname='vector'")
                    ).scalar()
                )
        except Exception as exc:  # noqa: BLE001
            HAS_PGVECTOR = False
            logger.warning(
                "CREATE EXTENSION vector failed (%s: %s) — vector columns will "
                "not be available and search falls back to Python cosine. "
                "Install the pgvector extension for this server.",
                type(exc).__name__,
                exc,
            )

    from app import models  # noqa: F401  register tables before create_all

    Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


__all__ = ["Base", "DATA_DIR", "DB_BACKEND", "HAS_PGVECTOR", "SessionLocal",
           "Session", "engine", "get_db", "has_pgvector", "init_db"]
