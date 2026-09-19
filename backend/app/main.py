from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import DB_BACKEND, HAS_PGVECTOR, SessionLocal, init_db
from app.models import User


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="ResumeAI", version="1.0.0", lifespan=lifespan)

# Vite proxies /api in dev, so CORS is a safety net, not the mechanism.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    db = SessionLocal()
    try:
        users = db.query(User).count()
    except Exception:
        users = -1
    finally:
        db.close()
    from app.services.storage import storage_mode
    return {
        "status": "ok",
        "database": DB_BACKEND,
        "pgvector": HAS_PGVECTOR,
        "storage": storage_mode(),
        "llm_configured": settings.llm_configured,
        "model": settings.model_default,
        "users": users,
    }