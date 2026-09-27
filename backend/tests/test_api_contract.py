"""Acceptance gate 5 — the whole surface works with no API key."""

import uuid

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
    assert keys == {
        "modern",
        "classic",
        "compact",
        "executive",
        "minimal",
        "balanced",
        "technical",
    }


def test_templates_carry_design_tokens(client):
    """design_tokens drives both the live preview and the PDF.

    An empty dict used to mean every template rendered identically, so assert
    the contract explicitly rather than only counting rows.
    """
    templates = client.get("/api/templates").json()
    assert templates, "no templates seeded"

    required = {
        "font",
        "heading",
        "name_align",
        "accent",
        "density",
        "caps",
        "divider",
        "header",
        "entry",
        "skill_columns",
    }
    for template in templates:
        tokens = template["design_tokens"]
        assert tokens, f"{template['key']} has empty design_tokens"
        assert required <= tokens.keys(), (
            f"{template['key']} missing {sorted(required - tokens.keys())}"
        )
        assert tokens["font"] in {"sans", "serif"}
        assert tokens["heading"] in {"rule", "underline", "plain", "boxed", "sidebar"}
        assert tokens["name_align"] in {"left", "center"}
        assert tokens["density"] in {"airy", "normal", "dense"}
        assert tokens["header"] in {"stacked", "split"}
        assert tokens["entry"] in {"stacked", "inline"}
        assert tokens["skill_columns"] in {1, 2, 3}
        # A split header only makes sense with a left-aligned name: centring
        # the name and floating contact right would collide.
        if tokens["header"] == "split":
            assert tokens["name_align"] == "left", (
                f"{template['key']} pairs a split header with a centred name"
            )

    # The choice has to be visible: templates that render the same are not
    # really choices.
    fingerprints = {
        tuple(sorted(t["design_tokens"].items(), key=lambda kv: kv[0])) for t in templates
    }
    assert len(fingerprints) == len(templates), "two templates render identically"


def test_reseed_updates_template_tokens(client):
    """Seeding twice must refresh presentation, not skip it.

    Templates are presentation rather than user data, so unlike users and
    resumes they are updated in place. Without this an existing database keeps
    the old empty design_tokens forever — which is exactly the state every
    database created before this change is in.
    """
    from app.models import Template
    from app.services.seed import TEMPLATES, seed

    db = client._session_factory()
    try:
        # Simulate a database seeded by the previous insert-only version.
        row = db.query(Template).filter(Template.key == "modern").one()
        row.design_tokens = {}
        row.description = "stale"
        db.commit()

        created = seed(db)
        db.commit()

        refreshed = db.query(Template).filter(Template.key == "modern").one()
        assert refreshed.design_tokens, "re-seed did not restore design_tokens"
        # Compare against the source of truth rather than a copied string, so
        # editing a description does not break this test for the wrong reason.
        expected = next(d for k, _n, d, _t in TEMPLATES if k == "modern")
        assert refreshed.description == expected
        # Updates must not be counted as inserts.
        assert created["templates"] == 0
    finally:
        db.close()


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


def test_pdf_export_differs_per_template(client, priya, priya_resume):
    """Switching template must change the downloaded PDF.

    Two regressions are covered here. The renderer used to hardcode Helvetica
    and ignore the template entirely, and the export cache key omitted the
    template, so even a style-aware renderer would have served the first PDF
    back from the bucket forever.
    """
    headers = {"X-User-Id": priya}
    renders = {}
    for key in ("modern", "classic", "executive", "technical"):
        applied = client.post(
            f"/api/resumes/{priya_resume}/template",
            json={"template_key": key},
            headers=headers,
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["template_key"] == key

        pdf = client.get(
            f"/api/resumes/{priya_resume}/export?format=pdf", headers=headers
        )
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF")
        renders[key] = pdf.content

    assert len({bytes(v) for v in renders.values()}) == len(renders), (
        "templates produced identical PDFs — design_tokens are not reaching the renderer"
    )


def test_txt_export_ignores_template(client, priya, priya_resume):
    """Plain text is for ATS paste boxes: styling must NOT leak into it."""
    headers = {"X-User-Id": priya}
    outputs = []
    for key in ("modern", "executive"):
        client.post(
            f"/api/resumes/{priya_resume}/template",
            json={"template_key": key},
            headers=headers,
        )
        outputs.append(
            client.get(
                f"/api/resumes/{priya_resume}/export?format=txt", headers=headers
            ).content
        )
    assert outputs[0] == outputs[1]


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


def test_parser_extracts_headline():
    """The line under the name is the professional title.

    Every real-world resume layout shows one, so structured_data needs the
    field populated for uploads, not just for the seeded demo resume.
    """
    from app.services.parsing import structure_text

    data = structure_text(
        "Michael J. Thompson\n"
        "Dental Office Manager\n"
        "michael@email.com | 315-555-7890\n"
        "\nWORK EXPERIENCE\n- Ran the practice\n"
    )
    assert data["contact"]["name"] == "Michael J. Thompson"
    assert data["contact"]["headline"] == "Dental Office Manager"


def test_parser_does_not_mistake_contact_for_headline():
    """Guard the heuristic: a contact line must never become the title."""
    from app.services.parsing import structure_text

    for second in ("jane@example.com", "+1 555 123 4567", "sara@x.com | 555-1234", "SUMMARY"):
        data = structure_text(f"Jane Doe\n{second}\n")
        assert data["contact"]["headline"] == "", f"{second!r} was promoted to headline"


def test_pdf_respects_serif_font(client, priya_resume):
    """Regression: entry blocks used to hardcode Helvetica.

    The font was applied to headings but every experience and project row
    called set_font("Helvetica", ...) literally, so serif templates silently
    reverted to sans mid-document.
    """
    import re

    client.post(f"/api/resumes/{priya_resume}/template", json={"template_key": "classic"})
    pdf = client.get(f"/api/resumes/{priya_resume}/export?format=pdf").content

    fonts = set(re.findall(rb"/BaseFont\s*/([A-Za-z-]+)", pdf))
    assert fonts, "no fonts embedded in the PDF"
    assert not any(b"Helvetica" in f for f in fonts), (
        f"serif template still emits Helvetica: {sorted(fonts)}"
    )


# ---------------------------------------------------------------- forking


def test_fork_copies_content_and_leaves_base_untouched(client, priya, priya_resume):
    headers = {"X-User-Id": priya}
    base = client.get(f"/api/resumes/{priya_resume}", headers=headers).json()

    response = client.post(
        f"/api/resumes/{priya_resume}/fork",
        headers=headers,
        json={"title": "Tailored for Acme", "tailored_for": "SRE at Acme"},
    )
    assert response.status_code == 201
    child = response.json()

    assert child["id"] != base["id"]
    assert child["structured_data"] == base["structured_data"]
    assert child["template_key"] == base["template_key"]
    assert child["kind"] == "tailored"
    assert child["parent_id"] == base["id"]
    assert child["tailored_for"] == "SRE at Acme"

    # Editing the child must not reach the parent.
    edited = dict(base["structured_data"])
    edited["summary"] = {"text": "Rewritten for the child only."}
    client.put(
        f"/api/resumes/{child['id']}/data", headers=headers, json={"structured_data": edited}
    )
    after = client.get(f"/api/resumes/{priya_resume}", headers=headers).json()
    assert after["structured_data"] == base["structured_data"], "fork leaked into the base"


def test_fork_copies_grounding_vectors(client, priya, priya_resume):
    """The subtle one: a fork with no vectors silently loses grounding.

    write_suggestions retrieves bullets scoped to resume_id, so an unindexed
    fork produces weaker suggestions with no error anywhere.
    """
    from app.models import VectorDoc

    headers = {"X-User-Id": priya}
    child = client.post(f"/api/resumes/{priya_resume}/fork", headers=headers, json={}).json()

    db = client._session_factory()
    try:
        source_n = (
            db.query(VectorDoc).filter(VectorDoc.resume_id == uuid.UUID(priya_resume)).count()
        )
        child_n = (
            db.query(VectorDoc).filter(VectorDoc.resume_id == uuid.UUID(child["id"])).count()
        )
    finally:
        db.close()

    assert source_n > 0, "fixture resume has no vectors; test cannot prove anything"
    assert child_n == source_n, f"fork has {child_n} vectors, source has {source_n}"


def test_uploaded_resume_is_indexed(client, priya):
    """Regression: index_resume used to be called only by the seeder, so
    every uploaded resume had zero grounding vectors."""
    from app.models import VectorDoc

    headers = {"X-User-Id": priya}
    body = (
        b"Jane Doe\nStaff Engineer\njane@example.com\n\n"
        b"EXPERIENCE\nAcme - Engineer - 2020\n- Built a distributed job scheduler\n"
        b"- Cut deploy time by 40 percent\n"
    )
    resume = client.post(
        "/api/resumes/upload",
        headers=headers,
        files={"file": ("cv.txt", body, "text/plain")},
        data={"title": "Indexed"},
    ).json()

    db = client._session_factory()
    try:
        n = db.query(VectorDoc).filter(VectorDoc.resume_id == uuid.UUID(resume["id"])).count()
    finally:
        db.close()
    assert n > 0, "uploaded resume was never indexed"


def test_deleting_a_fork_keeps_the_parent_file(client, priya, priya_resume):
    """A fork shares its parent's storage_key; deleting it by prefix would
    destroy the file the parent still parses from."""
    headers = {"X-User-Id": priya}
    child = client.post(f"/api/resumes/{priya_resume}/fork", headers=headers, json={}).json()

    assert client.delete(f"/api/resumes/{child['id']}", headers=headers).status_code == 200
    base = client.get(f"/api/resumes/{priya_resume}", headers=headers)
    assert base.status_code == 200
    assert base.json()["structured_data"], "parent lost its content"


def test_deleting_base_orphans_children_rather_than_cascading(client, priya, priya_resume):
    """Deleting a base must never silently delete the tailored versions the
    user already sent to employers."""
    headers = {"X-User-Id": priya}
    child = client.post(f"/api/resumes/{priya_resume}/fork", headers=headers, json={}).json()

    client.delete(f"/api/resumes/{priya_resume}", headers=headers)

    survivor = client.get(f"/api/resumes/{child['id']}", headers=headers)
    assert survivor.status_code == 200, "deleting the base cascaded into its fork"
    assert survivor.json()["parent_id"] is None


def test_fork_is_user_scoped(client, arjun, priya_resume):
    response = client.post(
        f"/api/resumes/{priya_resume}/fork", headers={"X-User-Id": arjun}, json={}
    )
    assert response.status_code == 404


def test_rename_resume(client, priya, priya_resume):
    headers = {"X-User-Id": priya}
    renamed = client.patch(
        f"/api/resumes/{priya_resume}", headers=headers, json={"title": "Renamed"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Renamed"

    blank = client.patch(
        f"/api/resumes/{priya_resume}", headers=headers, json={"title": "   "}
    )
    assert blank.status_code in (400, 422), "empty title should be rejected"


def test_create_blank_resume(client, priya):
    response = client.post(
        "/api/resumes", headers={"X-User-Id": priya}, json={"title": "From scratch"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "From scratch"
    assert body["kind"] == "base"
    assert body["parse_status"] == "ready"


def test_list_includes_score_and_kind(client, priya, priya_resume):
    """The library card needs a score without an N+1 fetch per card."""
    headers = {"X-User-Id": priya}
    client.post(f"/api/resumes/{priya_resume}/fork", headers=headers, json={})

    rows = client.get("/api/resumes", headers=headers).json()
    assert len(rows) >= 2
    for row in rows:
        assert "overall_score" in row
        assert row["kind"] in {"base", "tailored"}
        assert "structured_data" in row, "thumbnail needs content"

    # Never analysed -> None, not 0. A 0% ring would read as a terrible resume.
    assert all(r["overall_score"] is None for r in rows)

    client.post(f"/api/resumes/{priya_resume}/analyze", headers=headers)
    rows = client.get("/api/resumes", headers=headers).json()
    scored = next(r for r in rows if r["id"] == priya_resume)
    assert isinstance(scored["overall_score"], int)


def test_tailoring_forks_by_default(client, priya, priya_resume):
    headers = {"X-User-Id": priya}
    session = client.post(
        f"/api/resumes/{priya_resume}/tailor",
        headers=headers,
        json={"jd_title": "SRE at Globex", "jd_content": "Terraform Kubernetes AWS"},
    ).json()

    assert session["resume_id"] != priya_resume, "tailoring mutated the base"

    child = client.get(f"/api/resumes/{session['resume_id']}", headers=headers).json()
    assert child["kind"] == "tailored"
    assert child["tailored_for"] == "SRE at Globex"
    assert child["parent_id"] == priya_resume


def test_tailoring_can_opt_out_of_forking(client, priya, priya_resume):
    session = client.post(
        f"/api/resumes/{priya_resume}/tailor",
        headers={"X-User-Id": priya},
        json={"jd_content": "Terraform Kubernetes", "fork": False},
    ).json()
    assert session["resume_id"] == priya_resume
