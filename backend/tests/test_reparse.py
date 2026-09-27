"""Increment C — column-aware PDF extraction and the re-parse path."""

import io

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.services.parsing import _column_split_x, extract_text, structure_text

LEFT = [
    "EXPERIENCE",
    "Acme Corp - Senior Engineer",
    "Jan 2020 - Present",
    "- Led the Kubernetes migration.",
    "- Built Terraform modules.",
]
RIGHT = ["SKILLS", "Kubernetes, Docker", "Terraform, AWS", "", "EDUCATION", "State University"]


def _two_column_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 10)
    _, height = letter
    for i in range(max(len(LEFT), len(RIGHT))):
        y = height - 100 - i * 16
        if i < len(LEFT):
            c.drawString(60, y, LEFT[i])
        if i < len(RIGHT):
            c.drawString(330, y, RIGHT[i])
    c.showPage()
    c.save()
    return buf.getvalue()


def _single_column_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 10)
    lines = [
        "Jane Doe",
        "jane@example.com",
        "EXPERIENCE",
        "Acme Corp - Senior Engineer - Jan 2020 - Present",
        "- Led the Kubernetes migration across 40 services.",
        "- Built Terraform modules adopted by 8 teams.",
    ]
    for i, t in enumerate(lines):
        c.drawString(60, 720 - i * 16, t)
    c.showPage()
    c.save()
    return buf.getvalue()


# ------------------------------------------------------- column detection
def test_two_column_page_is_detected():
    import pdfplumber

    with pdfplumber.open(io.BytesIO(_two_column_pdf())) as doc:
        assert _column_split_x(doc.pages[0]) is not None


def test_single_column_page_is_not_split():
    import pdfplumber

    with pdfplumber.open(io.BytesIO(_single_column_pdf())) as doc:
        assert _column_split_x(doc.pages[0]) is None


def test_two_column_pdf_reads_each_column_in_order():
    """Before: rows interleaved, e.g. 'EXPERIENCE' then 'SKILLS'."""
    raw = extract_text("cv.pdf", _two_column_pdf())
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    assert lines.index("Acme Corp - Senior Engineer") < lines.index("SKILLS")
    assert lines.index("- Built Terraform modules.") < lines.index("SKILLS")


def test_two_column_pdf_parses_into_the_right_sections():
    data = structure_text(extract_text("cv.pdf", _two_column_pdf()))
    assert len(data["experience"]) == 1
    entry = data["experience"][0]
    assert entry["company"] == "Acme Corp"
    assert entry["role"] == "Senior Engineer"
    assert len(entry["bullets"]) == 2
    assert data["skills"], "right-hand column should still produce skills"
    assert data["education"], "right-hand column should still produce education"


def test_single_column_pdf_is_unaffected():
    data = structure_text(extract_text("cv.pdf", _single_column_pdf()))
    assert data["experience"][0]["company"] == "Acme Corp"
    assert len(data["experience"][0]["bullets"]) == 2


def test_a_corrupt_pdf_surfaces_as_a_failed_parse(client, priya):
    """A broken file must become parse_status=failed, not a 500."""
    resp = client.post(
        "/api/resumes/upload",
        files={"file": ("broken.pdf", b"not a pdf at all", "application/pdf")},
        data={"title": "Corrupt"},
        headers={"X-User-Id": priya},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["parse_status"] == "failed"
    assert body["parse_note"]


# -------------------------------------------------------------- re-parse
TXT = b"""Jane Doe
jane@example.com

EXPERIENCE
Acme Corp - Senior Engineer
Jan 2020 - Present
- Led the platform migration to Kubernetes across 40 services, cutting
  deploy time by 60% for every downstream team.
- Built Terraform modules.
"""

LEGACY_BAD_PARSE = {
    "contact": {"name": "Jane Doe"},
    "summary": {"text": ""},
    "experience": [
        {
            "company": "",
            "role": "",
            "dates": "Jan 2020 - Present",
            "bullets": ["Led the platform migration to Kubernetes across 40 services, cutting"],
        },
        {
            "company": "deploy time by 60% for every downstream team.",
            "role": "",
            "dates": "",
            "bullets": ["Built Terraform modules."],
        },
    ],
    "skills": [],
    "education": [],
    "projects": [],
}


@pytest.fixture()
def user_headers(priya):
    return {"X-User-Id": priya}


@pytest.fixture()
def other_user_headers(arjun):
    return {"X-User-Id": arjun}


@pytest.fixture()
def uploaded(client, user_headers):
    resp = client.post(
        "/api/resumes/upload",
        files={"file": ("cv.txt", TXT, "text/plain")},
        data={"title": "Reparse probe"},
        headers=user_headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def test_reparse_repairs_a_legacy_bad_parse(client, user_headers, uploaded):
    client.put(
        f"/api/resumes/{uploaded}/data",
        json={"structured_data": LEGACY_BAD_PARSE},
        headers=user_headers,
    )
    resp = client.post(f"/api/resumes/{uploaded}/reparse", headers=user_headers)
    assert resp.status_code == 200
    entries = resp.json()["structured_data"]["experience"]
    assert len(entries) == 1
    assert entries[0]["company"] == "Acme Corp"
    assert entries[0]["role"] == "Senior Engineer"
    assert len(entries[0]["bullets"]) == 2


def test_reparse_is_undoable(client, user_headers, uploaded):
    """Re-parse discards manual edits, so they must be one undo away."""
    client.put(
        f"/api/resumes/{uploaded}/data",
        json={"structured_data": LEGACY_BAD_PARSE},
        headers=user_headers,
    )
    client.post(f"/api/resumes/{uploaded}/reparse", headers=user_headers)
    undone = client.post(f"/api/resumes/{uploaded}/undo", headers=user_headers)
    assert undone.status_code == 200
    assert undone.json()["structured_data"]["experience"][0]["company"] == ""


def test_reparse_rejects_a_resume_with_no_uploaded_file(client, user_headers):
    made = client.post("/api/resumes", json={"title": "Manual"}, headers=user_headers).json()
    resp = client.post(f"/api/resumes/{made['id']}/reparse", headers=user_headers)
    assert resp.status_code == 422
    assert "uploaded" in resp.json()["detail"]


def test_reparse_is_scoped_to_the_owner(client, user_headers, other_user_headers, uploaded):
    resp = client.post(f"/api/resumes/{uploaded}/reparse", headers=other_user_headers)
    assert resp.status_code == 404
