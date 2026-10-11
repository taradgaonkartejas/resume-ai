import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db as app_db
from app.config import settings


def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="also run tests marked `live`, which call real provider APIs "
             "(slow, spends quota, and results vary run to run).",
    )


def pytest_collection_modifyitems(config, items):
    """Live tests are opt-in.

    A default `pytest` run that quietly spends API quota and takes twelve
    minutes is not a unit-test suite. Deselecting `live` by default makes the
    default run hermetic and fast; `--live` (or `make test-live`) asks for
    the real thing deliberately.
    """
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live (calls a real provider API)")
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(skip)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: needs a real provider API key; exempt from key isolation",
    )


@pytest.fixture(autouse=True)
def _no_provider_keys(request, monkeypatch):
    """Clear every provider key unless the test asks for a live one.

    The suite was hermetic only by accident -- because the machine running it
    happened to have no keys. With keys present (the normal developer state)
    twenty-two tests failed and the run took 12.5 minutes, because tests that
    assert rule-engine behaviour silently reached real models instead:

        test_no_api_key_configured           keys were configured
        test_rule_writer_scores_exactly...   "fixed 5/7; expected exactly 2"
        test_rewrite_api.py (6 tests)        422 instead of 201
        test_lifecycle.py   (3 tests)        no suggestions produced

    None of those were regressions. They were the suite measuring the LLM
    while claiming to measure the rules -- the same defect already fixed in
    writer_report.py, in a second place.

    Provider.api_key is a property over settings, so clearing the settings
    fields genuinely disarms llm.is_configured() rather than only hiding the
    value.
    """
    if request.node.get_closest_marker("live"):
        return
    from app.ai import llm

    for provider in llm.PROVIDERS.values():
        monkeypatch.setattr(settings, provider.key_setting, "", raising=False)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """No test may write into the real `data/` directory.

    This is the fixture that should have existed from the start. Without it
    the suite writes its fixtures into the SAME directory that holds real
    uploads and exports, which caused, in order:

      * `storage prune` being unable to answer "is this file junk?", because
        `cv.txt` and a candidate's actual resume sat side by side;
      * a restored upload being silently overwritten by a `pytest` run, twice,
        while the restore was still being verified;
      * ultimately a blanket `rm -rf` of the whole directory, because the
        contents had become indistinguishable.

    `DATA_DIR` is imported as `from app.db import DATA_DIR` in two modules,
    so the name is bound in each of their namespaces at import time. Patching
    only `app.db.DATA_DIR` would leave `storage` and `graphs` still pointing
    at the real path -- the fix has to rebind every holder, which is why the
    list below is explicit and why the test beneath it exists.

    tmp_path is function-scoped, so tests are also isolated from each other.
    """
    import app.config as config_mod
    import app.db as db_mod
    from app.ai import graphs as graphs_mod
    from app.services import storage as storage_mod

    sandbox = tmp_path / "data"
    sandbox.mkdir(parents=True, exist_ok=True)
    for module in (db_mod, storage_mod, graphs_mod):
        monkeypatch.setattr(module, "DATA_DIR", sandbox, raising=True)

    # app/cli.py deliberately does NOT import DATA_DIR -- importing app.db
    # builds the engine at module scope, which the storage commands have to
    # survive -- so it derives `ROOT_DIR / "data"` instead. That is a second,
    # independent route to the same directory, and patching only DATA_DIR
    # left it pointing at the real one: the CLI read the sandbox while the
    # tests wrote to the real path, which both leaked files AND failed.
    #
    # Safe to patch: ROOT_DIR has no consumer in app/ other than these data
    # paths. cli.py imports it inside each function, so it sees this.
    monkeypatch.setattr(config_mod, "ROOT_DIR", tmp_path, raising=True)
    return sandbox


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
