from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    admin,
    analysis,
    chat,
    exports,
    health,
    resumes,
    suggestions,
    tailoring,
    templates,
    users,
    versions,
)
from app.db import init_db
from app.services.exceptions import (
    DomainError,
    NotFoundError,
    QuotaExceeded,
    ValidationError,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="ResumeAI", version="1.0.0", lifespan=lifespan)

# Vite proxies /api in dev, so CORS is a safety net rather than the mechanism.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Domain exceptions are translated once, here — services never import HTTP types.
@app.exception_handler(NotFoundError)
async def _not_found(request: Request, exc: NotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc) or "Not found"})


@app.exception_handler(QuotaExceeded)
async def _quota(request: Request, exc: QuotaExceeded):
    return JSONResponse(status_code=429, content={"detail": str(exc)})


@app.exception_handler(ValidationError)
async def _validation(request: Request, exc: ValidationError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(DomainError)
async def _domain(request: Request, exc: DomainError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


for module in (
    health,
    users,
    templates,
    resumes,
    analysis,
    tailoring,
    suggestions,
    chat,
    versions,
    exports,
    admin,
):
    app.include_router(module.router, prefix="/api")
