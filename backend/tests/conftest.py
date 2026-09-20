import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db as app_db


@pytest.fixture(scope="function")
def client(monkeypatch):
    """Isolated in-memory database per test.

    StaticPool is required: without it every connection gets its own empty
    in-memory database and the tables appear to vanish.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", TestSession)

    import app.api.health as health_mod

    monkeypatch.setattr(health_mod, "SessionLocal", TestSession)

    app_db.Base.metadata.create_all(engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    import app.main as main_mod

    main_mod.app.dependency_overrides[app_db.get_db] = override_get_db

    from app.services.seed import seed

    db = TestSession()
    seed(db)
    db.close()

    with TestClient(main_mod.app) as test_client:
        test_client._session_factory = TestSession
        yield test_client

    main_mod.app.dependency_overrides.clear()


@pytest.fixture
def users(client):
    rows = client.get("/api/users").json()
    return {row["name"]: row["id"] for row in rows}


@pytest.fixture
def priya(users):
    return users["Priya Sharma"]


@pytest.fixture
def arjun(users):
    return users["Arjun Mehta"]


@pytest.fixture
def priya_resume(client, priya):
    rows = client.get("/api/resumes", headers={"X-User-Id": priya}).json()
    return rows[0]["id"]


def new_resume(client, user_id: str, title: str, data: dict) -> str:
    """Create a resume directly through the service layer."""
    from app.repositories.resume_repository import ResumeRepository

    db = client._session_factory()
    try:
        repo = ResumeRepository(db)
        resume = repo.create(
            user_id=uuid.UUID(user_id),
            title=title,
            structured_data=data,
            parse_status="ready",
        )
        db.commit()
        return str(resume.id)
    finally:
        db.close()
