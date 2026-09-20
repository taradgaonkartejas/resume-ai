from fastapi import APIRouter

from app.config import settings
from app.db import DB_BACKEND, SessionLocal, has_pgvector
from app.models import User
from app.schemas import HealthOut
from app.services.storage import storage_mode

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    db = SessionLocal()
    try:
        users = db.query(User).count()
    except Exception:
        users = -1
    finally:
        db.close()
    return HealthOut(
        status="ok",
        database=DB_BACKEND,
        pgvector=has_pgvector(),
        storage=storage_mode(),
        llm_configured=settings.llm_configured,
        model=settings.model_default,
        users=users,
    )
