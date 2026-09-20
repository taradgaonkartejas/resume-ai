"""Acceptance gate 1 — per-user isolation.

Two users hold near-identical Kubernetes bullets. Neither may read, modify or
retrieve the other's data.
"""

import uuid

from tests.conftest import new_resume

ACME = {
    "contact": {"name": "Priya Sharma", "email": "priya@example.com"},
    "summary": {"text": "SRE at Acme running Kubernetes at scale."},
    "experience": [
        {
            "company": "Acme",
            "role": "SRE",
            "dates": "2021 - Present",
            "bullets": ["Led migration of 40 services to Kubernetes at Acme"],
        }
    ],
    "projects": [],
    "education": [],
    "skills": [{"label": "Platform", "items": ["Kubernetes"]}],
}

GLOBEX = {
    "contact": {"name": "Arjun Mehta", "email": "arjun@example.com"},
    "summary": {"text": "SRE at Globex running Kubernetes at scale."},
    "experience": [
        {
            "company": "Globex",
            "role": "SRE",
            "dates": "2021 - Present",
            "bullets": ["Led migration of 40 services to Kubernetes at Globex"],
        }
    ],
    "projects": [],
    "education": [],
    "skills": [{"label": "Platform", "items": ["Kubernetes"]}],
}


def test_list_is_scoped(client, priya, arjun):
    a = new_resume(client, priya, "Priya Acme", ACME)
    b = new_resume(client, arjun, "Arjun Globex", GLOBEX)

    priya_ids = {r["id"] for r in client.get("/api/resumes", headers={"X-User-Id": priya}).json()}
    arjun_ids = {r["id"] for r in client.get("/api/resumes", headers={"X-User-Id": arjun}).json()}

    assert a in priya_ids and a not in arjun_ids
    assert b in arjun_ids and b not in priya_ids
    assert priya_ids.isdisjoint(arjun_ids)


def test_cross_user_read_is_404_not_403(client, priya, arjun):
    """404, never 403 — a 403 would confirm the row exists."""
    b = new_resume(client, arjun, "Arjun Globex", GLOBEX)
    response = client.get(f"/api/resumes/{b}", headers={"X-User-Id": priya})
    assert response.status_code == 404


def test_cross_user_write_is_blocked(client, priya, arjun):
    b = new_resume(client, arjun, "Arjun Globex", GLOBEX)
    assert client.put(
        f"/api/resumes/{b}/data",
        headers={"X-User-Id": priya},
        json={"structured_data": ACME},
    ).status_code == 404
    assert client.delete(f"/api/resumes/{b}", headers={"X-User-Id": priya}).status_code == 404


def test_cross_user_suggestion_patch_is_blocked(client, priya, arjun):
    b = new_resume(client, arjun, "Arjun Globex", GLOBEX)
    session = client.post(
        f"/api/resumes/{b}/tailor",
        headers={"X-User-Id": arjun},
        json={"jd_title": "SRE", "jd_content": "Terraform AWS Prometheus Kubernetes GitOps"},
    ).json()
    buckets = client.get(
        f"/api/resumes/{b}/tailor/{session['id']}/suggestions",
        headers={"X-User-Id": arjun},
    ).json()
    assert buckets["active"], "expected at least one suggestion"
    suggestion_id = buckets["active"][0]["id"]

    hijack = client.patch(
        f"/api/suggestions/{suggestion_id}",
        headers={"X-User-Id": priya},
        json={"action": "accept"},
    )
    assert hijack.status_code == 404


def test_vector_scoping_never_crosses_users(client, priya, arjun):
    """Near-identical bullets must not leak between users."""
    from app.repositories.vector_repository import VectorRepository

    db = client._session_factory()
    try:
        repo = VectorRepository(db)
        repo.add_doc(
            corpus="resume_bullets",
            content="Led migration of 40 services to Kubernetes at Acme",
            embedding=[0.1] * 384,
            user_id=uuid.UUID(priya),
            resume_id=uuid.uuid4(),
        )
        repo.add_doc(
            corpus="resume_bullets",
            content="Led migration of 40 services to Kubernetes at Globex",
            embedding=[0.1] * 384,
            user_id=uuid.UUID(arjun),
            resume_id=uuid.uuid4(),
        )
        db.commit()

        priya_docs = repo.candidates("resume_bullets", user_id=uuid.UUID(priya))
        arjun_docs = repo.candidates("resume_bullets", user_id=uuid.UUID(arjun))

        # Each user sees their own marker and never the other's. Counts are not
        # asserted: the seed also indexes demo bullets into this corpus.
        assert any("Acme" in d.content for d in priya_docs)
        assert any("Globex" in d.content for d in arjun_docs)
        assert all("Globex" not in d.content for d in priya_docs)
        assert all("Acme" not in d.content for d in arjun_docs)

        # Every row handed back really is owned by the querying user.
        assert all(d.user_id == uuid.UUID(priya) for d in priya_docs)
        assert all(d.user_id == uuid.UUID(arjun) for d in arjun_docs)
    finally:
        db.close()


def test_global_corpus_requires_null_user(client, priya):
    import pytest

    from app.repositories.vector_repository import VectorRepository

    db = client._session_factory()
    try:
        repo = VectorRepository(db)
        with pytest.raises(ValueError):
            repo.add_doc(
                corpus="skill_taxonomy",
                content="kubernetes -> k8s",
                embedding=[0.0] * 384,
                user_id=uuid.UUID(priya),
            )
        with pytest.raises(ValueError):
            repo.add_doc(
                corpus="resume_bullets",
                content="orphan",
                embedding=[0.0] * 384,
                user_id=None,
            )
    finally:
        db.close()


def test_unknown_user_is_rejected(client):
    response = client.get("/api/resumes", headers={"X-User-Id": str(uuid.uuid4())})
    assert response.status_code == 404
