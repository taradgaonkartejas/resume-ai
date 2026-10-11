"""Increment A — parsing robustness and the parse-status guard.

Each test here corresponds to a bug that was reproduced against the running
code before the fix (see AI-QUALITY-PLAN.md §1).
"""

import io

import pytest
from docx import Document
from reportlab.pdfgen import canvas

from app.services.exceptions import ResumeNotReady
from app.services.parsing import (
    MIN_WORDS_PDF,
    _split_date_tail,
    extract_text,
    structure_text,
)


# ----------------------------------------------------------- §3.6 numbered
def test_numbered_bullets_are_recognised():
    """Before: `1.` / `2)` lists produced ZERO experience entries."""
    data = structure_text(
        "EXPERIENCE\n"
        "Acme Corp - Senior Engineer - 2020-2024\n"
        "1. Led the platform migration to Kubernetes.\n"
        "2) Built Terraform modules adopted by 8 teams.\n"
    )
    assert len(data["experience"]) == 1
    entry = data["experience"][0]
    assert entry["company"] == "Acme Corp"
    assert entry["bullets"] == [
        "Led the platform migration to Kubernetes.",
        "Built Terraform modules adopted by 8 teams.",
    ]


def test_glyph_bullets_still_work():
    """The numbered-bullet change must not regress the original formats."""
    data = structure_text(
        "EXPERIENCE\n"
        "Acme Corp - Senior Engineer - 2020-2024\n"
        "- Led the migration.\n"
        "\u2022 Built modules.\n"
        "* Shipped the thing.\n"
    )
    assert data["experience"][0]["bullets"] == [
        "Led the migration.",
        "Built modules.",
        "Shipped the thing.",
    ]


@pytest.mark.parametrize(
    "line",
    [
        "3.5x faster than the previous pipeline",  # decimal, no space after the dot
        "2024 - Present",                          # a bare date line
        "1000 requests per second sustained",      # a leading metric
    ],
)
def test_numeric_prose_is_not_mistaken_for_a_list_marker(line):
    """The numbered-bullet regex requires `<digits><.|)><space>`.

    Asserted against _BULLET_RE directly: routed through structure_text these
    lines hit the glyph branch or the header branch first, so the test would
    pass without ever exercising the numeric alternative.
    """
    from app.services.parsing import _BULLET_RE

    assert _BULLET_RE.match(line) is None


@pytest.mark.parametrize("line", ["1. Did a thing", "2) Did another", "10.  Spaced out"])
def test_real_numbered_markers_do_match(line):
    from app.services.parsing import _BULLET_RE

    assert _BULLET_RE.match(line) is not None


# ------------------------------------------------------------- §3.4 tables
def _docx_with_table() -> bytes:
    doc = Document()
    doc.add_paragraph("Jane Doe")
    doc.add_paragraph("SKILLS")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Languages"
    table.cell(0, 1).text = "Python, Go, Rust"
    table.cell(1, 0).text = "Cloud"
    table.cell(1, 1).text = "AWS, Kubernetes, Terraform"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize("skill", ["Python", "Go", "Rust", "AWS", "Kubernetes", "Terraform"])
def test_docx_table_content_survives_extraction(skill):
    """Before: document.paragraphs excluded table cells, losing the whole grid."""
    assert skill in extract_text("resume.docx", _docx_with_table())


def test_docx_paragraphs_still_extracted():
    assert "Jane Doe" in extract_text("resume.docx", _docx_with_table())


# --------------------------------------------------------- §3.2 scanned PDF
def _image_only_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.rect(100, 500, 300, 200, fill=1)  # no text operators at all
    c.showPage()
    c.save()
    return buf.getvalue()


def test_image_only_pdf_extracts_no_text():
    assert len(extract_text("scan.pdf", _image_only_pdf()).split()) < MIN_WORDS_PDF


def test_scanned_pdf_is_marked_failed_not_ready(tmp_path, monkeypatch):
    """Before: 0 chars + no exception => parse_status 'ready' + empty resume."""
    from app.services import parsing

    class _Resume:
        storage_key = "u/scan.pdf"
        raw_text = ""
        structured_data = None
        parse_status = "pending"
        parse_note = ""
        id = None

    class _Repo:
        class db:
            @staticmethod
            def commit():
                pass

        def get_owned(self, *_a, **_k):
            return _Resume()

    monkeypatch.setattr(parsing.storage, "get_object", lambda *_a, **_k: _image_only_pdf())
    svc = parsing.ParsingService.__new__(parsing.ParsingService)
    svc.resumes = _Repo()
    svc.vectors = None

    out = svc.parse_resume("rid", "uid")
    assert out.parse_status == "failed"
    assert "scanned image" in out.parse_note


# ------------------------------------------------------- §1.9 status guard
@pytest.mark.parametrize("status", ["pending", "failed"])
def test_analysis_refuses_an_unparsed_resume(status):
    from app.services.analysis_service import _require_parsed

    class _R:
        parse_status = status
        parse_note = "boom"

    with pytest.raises(ResumeNotReady):
        _require_parsed(_R())


def test_analysis_allows_a_ready_resume():
    from app.services.analysis_service import _require_parsed

    class _R:
        parse_status = "ready"
        parse_note = ""

    _require_parsed(_R())  # must not raise


def test_failed_resume_message_includes_the_parse_note():
    from app.services.analysis_service import _require_parsed

    class _R:
        parse_status = "failed"
        parse_note = "No readable text found - this file may be a scanned image."

    with pytest.raises(ResumeNotReady, match="scanned image"):
        _require_parsed(_R())


# ------------------------------------ the guard must not flunk short text files
@pytest.mark.parametrize(
    "name,text,should_fail",
    [
        ("cv.txt", "Jane Doe\njane@x.com\nEXPERIENCE\nAcme - Eng\n- Did work", False),
        ("cv.txt", "Priya Sharma\npriya@x.com\nExperienced engineer.", False),
        ("cv.txt", "", True),
        ("cv.docx", "Jane Doe jane@x.com Engineer", False),
        ("scan.pdf", "Jane Doe", True),      # a PDF this empty is a scan
        ("real.pdf", " ".join(["word"] * 40), False),
    ],
)
def test_extraction_failure_is_format_aware(name, text, should_fail):
    """"May be a scanned image" is only a sane diagnosis for a PDF."""
    from app.services.parsing import _extraction_failure

    assert bool(_extraction_failure(name, text)) is should_fail


def test_scanned_pdf_note_differs_from_empty_file_note():
    from app.services.parsing import _extraction_failure

    assert "scanned image" in _extraction_failure("scan.pdf", "Jane Doe")
    assert "scanned image" not in _extraction_failure("cv.txt", "")


# ---------------------------------------------------------------------------
# One real resume that parsed into two employers and six schools
# ---------------------------------------------------------------------------

#: Lifted verbatim from Tejas_Taradgaonkar_Resume_react_java.pdf, which
#: reproduced every defect below. Stored as text, not as a PDF: the bugs are
#: in structure_text, and a binary fixture would make this depend on
#: pdfplumber's layout engine instead of on the thing under test.
#: NOTE: the long lines below are the fixture. Where a line wraps is
#: precisely what is under test, so they are marked rather than reflowed.
_REAL = "\n".join([
    "TEJAS SURAJ TARADGAONKAR",
    "",
    "EXPERIENCE",
    "Software Engineer | Ceinsys Tech Ltd. Apr 2024 – Present",
    "• Architected a RabbitMQ-based microservices pipeline using version control best practices to manage code changes,",  # noqa: E501
    "implementing prefetch-controlled consumers and heartbeat-safe threading to lift throughput to 10 jobs/hour.",  # noqa: E501
    "• Engineered Java Full Stack solutions, designing and shipping RESTful APIs and service-to-service integrations across",  # noqa: E501
    "Java/Spring Boot and Python/FastAPI services, while performing backend development to optimize PostgreSQL/PostGIS",  # noqa: E501
    "queries and cut p95 response time by 100%.",
    "• Standardized deployment by containerizing the pipeline with Docker, resolving runtime ABI/dependency mismatches to",  # noqa: E501
    "cut build failures by 20%, while safeguarding data integrity with transactional ORM patterns and sanitized import",  # noqa: E501
    "handling that eliminated encoding/null-byte corruption.",
    "EDUCATION",
    "Post Graduate Diploma in Advanced Computing (PG-DAC) — CDAC, Pune 2024 | Pune",
    "Score: 73%",
    "B.E. (Mechanical) — Vishwakarma Institute of Technology (VIT) 2020 | Pune",
    "CGPA: 7.79",
    "XII (HSC) — Modern College of Science, Commerce & Arts 2016 | Pune",
    "Score: 67.85%",
    "",
])

def test_a_bullet_that_wraps_onto_a_capital_is_not_a_new_employer():
    """The reported bug: one job parsed as two.

    Bullet two wraps onto "Java/Spring Boot ...". The continuation test only
    accepted a line starting lowercase, so the capital J closed the entry and
    the next fragment -- "queries and cut p95 response time by 100%." --
    became a company.
    """
    exp = structure_text(_REAL)["experience"]
    assert len(exp) == 1, [e["company"] for e in exp]
    assert exp[0]["bullets"][1].endswith("cut p95 response time by 100%.")
    assert len(exp[0]["bullets"]) == 3


def test_role_and_company_are_ordered_by_evidence_not_position():
    """`Role | Company` is at least as common as `Company | Role`."""
    entry = structure_text(_REAL)["experience"][0]
    assert entry["company"] == "Ceinsys Tech Ltd."
    assert entry["role"] == "Software Engineer"
    assert entry["dates"] == "Apr 2024 – Present"


def test_the_company_first_order_still_parses_the_same_way():
    """The fix may only correct an unambiguous case; a tie keeps the order."""
    entry = structure_text(
        "EXPERIENCE\nAcme Corp | Senior Engineer Jan 2020 - Dec 2023\n• Did a thing.\n"
    )["experience"][0]
    assert entry["company"] == "Acme Corp"
    assert entry["role"] == "Senior Engineer"


def test_a_grade_line_is_a_property_of_the_school_above_it():
    """"Score: 73%" was becoming a school of its own -- and reaching exports."""
    edu = structure_text(_REAL)["education"]
    assert len(edu) == 3, [e["school"] for e in edu]
    # The measure is kept only when the number cannot speak for itself:
    # "73%" is self-describing, a bare "7.79" is not, so CGPA is preserved.
    assert [e["grade"] for e in edu] == ["73%", "CGPA 7.79", "67.85%"]


def test_school_degree_dates_and_location_each_land_in_their_own_field():
    first = structure_text(_REAL)["education"][0]
    assert first["school"] == "CDAC, Pune"
    assert first["degree"] == "Post Graduate Diploma in Advanced Computing (PG-DAC)"
    assert first["dates"] == "2024"
    assert first["location"] == "Pune"


def test_a_city_before_a_year_is_not_read_as_a_month():
    """`[A-Za-z]{3,9}\\s?\\d{4}` matched "Pune 2024" and called it a date."""
    assert _split_date_tail("CDAC, Pune 2024") == ("CDAC, Pune", "2024")
    assert _split_date_tail("Acme Corp Apr 2024 – Present") == (
        "Acme Corp",
        "Apr 2024 – Present",
    )
