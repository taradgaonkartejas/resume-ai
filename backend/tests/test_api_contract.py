"""Acceptance gate 5 — the whole surface works with no API key."""

from sqlalchemy import func, select

from app.config import settings
from app.models import VectorDoc


def test_no_api_key_configured():
    assert settings.llm_configured is False


def test_health_reports_every_subsystem(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["database"] in ("postgresql", "sqlite")
    assert body["storage"] in ("minio", "disk")
    assert body["llm_configured"] is False
    assert body["users"] == 5


def test_all_documented_endpoints_are_registered(client):
    paths = client.get("/openapi.json").json()["paths"]
    expected = [
        ("get", "/api/health"),
        ("get", "/api/users"),
        ("get", "/api/templates"),
        ("get", "/api/sample-jd"),
        ("get", "/api/resumes"),
        ("post", "/api/resumes/upload"),
        ("get", "/api/resumes/{resume_id}"),
        ("get", "/api/resumes/{resume_id}/parse-status"),
        ("put", "/api/resumes/{resume_id}/data"),
        ("post", "/api/resumes/{resume_id}/template"),
        ("delete", "/api/resumes/{resume_id}"),
        ("post", "/api/resumes/{resume_id}/analyze"),
        ("get", "/api/resumes/{resume_id}/analysis"),
        ("post", "/api/resumes/{resume_id}/tailor"),
        ("get", "/api/resumes/{resume_id}/tailor"),
        ("get", "/api/resumes/{resume_id}/tailor/{session_id}/suggestions"),
        ("patch", "/api/suggestions/{suggestion_id}"),
        ("get", "/api/resumes/{resume_id}/chat"),
        ("post", "/api/resumes/{resume_id}/chat"),
        ("get", "/api/resumes/{resume_id}/versions"),
        ("post", "/api/resumes/{resume_id}/undo"),
        ("post", "/api/resumes/{resume_id}/redo"),
        ("get", "/api/resumes/{resume_id}/export"),
        ("get", "/api/admin/agent-runs"),
    ]
    for method, path in expected:
        assert path in paths, f"missing path {path}"
        assert method in paths[path], f"missing {method.upper()} {path}"


def test_seed_is_idempotent(client):
    from app.services.seed import seed

    db = client._session_factory()
    try:
        created = seed(db)
        # No new rows for anything keyed by identity.
        assert created["users"] == 0
        assert created["templates"] == 0
        assert created["resumes"] == 0
        assert created["job_descriptions"] == 0
        # Vector docs are re-indexed rather than duplicated: index_resume
        # deletes before inserting, so the table does not grow.
        before = db.scalar(select(func.count()).select_from(VectorDoc))
        seed(db)
        after = db.scalar(select(func.count()).select_from(VectorDoc))
        assert after == before
    finally:
        db.close()


def test_user_switcher_payload(client):
    users = client.get("/api/users").json()
    assert len(users) == 5
    assert {u["name"] for u in users} == {
        "Priya Sharma",
        "Arjun Mehta",
        "Sara Iyer",
        "Daniel Okafor",
        "Lena Fischer",
    }
    assert all(u["chat_tokens_left"] == 25 for u in users)


def test_templates_seeded(client):
    keys = {t["key"] for t in client.get("/api/templates").json()}
    assert keys == {"modern", "classic", "compact", "executive", "minimal"}


def test_missing_header_falls_back_to_first_user(client, priya):
    assert client.get("/api/resumes").status_code == 200


def test_malformed_user_id_is_400(client):
    assert client.get("/api/resumes", headers={"X-User-Id": "not-a-uuid"}).status_code == 400


def test_exports_render_in_all_formats(client, priya, priya_resume):
    headers = {"X-User-Id": priya}
    for fmt, magic in (("pdf", b"%PDF"), ("docx", b"PK"), ("txt", b"")):
        response = client.get(
            f"/api/resumes/{priya_resume}/export?format={fmt}", headers=headers
        )
        assert response.status_code == 200, fmt
        assert len(response.content) > 0
        if magic:
            assert response.content.startswith(magic), fmt


def test_export_rejects_unknown_format(client, priya, priya_resume):
    response = client.get(
        f"/api/resumes/{priya_resume}/export?format=rtf", headers={"X-User-Id": priya}
    )
    assert response.status_code == 422


def test_upload_parses_a_text_resume(client, priya):
    content = b"""Jane Roe
jane@example.com | +1 555 0100 | Berlin

SUMMARY
Backend engineer focused on distributed systems.

EXPERIENCE
Initech - Senior Engineer - Jan 2020 - Present
- Built event pipeline processing 2M messages per day

SKILLS
Languages: Python, Go
"""
    response = client.post(
        "/api/resumes/upload",
        headers={"X-User-Id": priya},
        files={"file": ("jane.txt", content, "text/plain")},
        data={"title": "Jane Roe CV"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["parse_status"] == "ready"
    assert body["structured_data"]["contact"]["email"] == "jane@example.com"
    assert body["structured_data"]["experience"]


def test_upload_rejects_unsupported_format(client, priya):
    response = client.post(
        "/api/resumes/upload",
        headers={"X-User-Id": priya},
        files={"file": ("x.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 422


def test_delete_purges_resume(client, priya, priya_resume):
    headers = {"X-User-Id": priya}
    body = client.delete(f"/api/resumes/{priya_resume}", headers=headers).json()
    assert body["deleted"] == priya_resume
    assert client.get(f"/api/resumes/{priya_resume}", headers=headers).status_code == 404
