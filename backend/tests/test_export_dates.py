"""Exported documents must show the dates the editor actually holds.

The date split (free-text `dates` -> start/end/current) changed the value every
renderer reads. These tests are the proof that the split did not quietly break
PDF/DOCX/TXT output, and they caught a real regression when it was written:
`date_label()` joins with an en-dash, which fpdf2's Latin-1 core fonts used to
render as a literal "?".
"""

import io

import pytest
from pypdf import PdfReader

from app.services.export_service import ExportService, _latin1
from app.services.resume_ops import date_label, migrate, to_plain_text
from tests.conftest import new_resume

TOKEN_SETS = {
    "modern": {"font": "sans", "heading": "rule", "name_align": "left",
               "accent": "#2563eb", "density": "normal", "caps": True,
               "divider": True, "header": "stacked", "entry": "stacked",
               "skill_columns": 2},
    "executive": {"font": "serif", "heading": "underline", "name_align": "center",
                  "accent": "", "density": "airy", "caps": True, "divider": True,
                  "header": "stacked", "entry": "stacked", "skill_columns": 1},
    "balanced": {"font": "sans", "heading": "plain", "name_align": "left",
                 "accent": "#16a34a", "density": "normal", "caps": False,
                 "divider": False, "header": "split", "entry": "inline",
                 "skill_columns": 2},
    "classic": {"font": "serif", "heading": "rule", "name_align": "center",
                "accent": "", "density": "normal", "caps": True, "divider": True,
                "header": "stacked", "entry": "stacked", "skill_columns": 1},
    "minimal": {"font": "sans", "heading": "plain", "name_align": "left",
                "accent": "", "density": "airy", "caps": False, "divider": False,
                "header": "stacked", "entry": "stacked", "skill_columns": 1},
    "compact": {"font": "sans", "heading": "boxed", "name_align": "left",
                "accent": "#7c3aed", "density": "dense", "caps": True,
                "divider": False, "header": "split", "entry": "inline",
                "skill_columns": 3},
    "technical": {"font": "sans", "heading": "sidebar", "name_align": "left",
                  "accent": "#ea580c", "density": "dense", "caps": False,
                  "divider": True, "header": "split", "entry": "inline",
                  "skill_columns": 2},
}

RESUME = migrate({
    "contact": {"name": "Priya Sharma", "headline": "SRE",
                "email": "priya@example.com", "phone": "+91 98765 43210",
                "location": "Pune, India", "links": ["github.com/priya"]},
    "summary": {"text": "Site reliability engineer with six years of experience."},
    "experience": [
        {"company": "Acme Corp", "role": "Senior SRE", "start_date": "Mar 2021",
         "end_date": "", "current": True,
         "bullets": ["Led migration of 40 services, cutting deploy time by 65%"]},
        {"company": "Globex", "role": "Infrastructure Engineer",
         "start_date": "Jul 2019", "end_date": "Feb 2021", "current": False,
         "bullets": ["Automated failover, improving recovery to under 2 minutes"]},
    ],
    "projects": [{"name": "kubewatch", "bullets": ["Reports cluster drift"]}],
    "education": [{"school": "COEP Pune", "degree": "B.Tech",
                   "start_date": "2015", "end_date": "2019"}],
    "skills": [{"label": "Platform", "items": ["Kubernetes", "Terraform"]}],
})


def _svc() -> ExportService:
    return ExportService.__new__(ExportService)


def _expected_labels() -> list[str]:
    return [date_label(e) for e in RESUME["experience"] + RESUME["education"]]


def _pdf_text(pdf: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    return " ".join(" ".join(p.extract_text().split()) for p in reader.pages)


@pytest.mark.parametrize("template_key", sorted(TOKEN_SETS))
def test_every_template_renders_the_date_range(template_key):
    text = _pdf_text(_svc()._render_pdf(RESUME, TOKEN_SETS[template_key]))
    for label in _expected_labels():
        # The PDF holds the Latin-1 form of the label.
        assert _latin1(label) in text, (
            f"{template_key}: {label!r} missing from the rendered PDF"
        )


@pytest.mark.parametrize("template_key", sorted(TOKEN_SETS))
def test_no_template_emits_replacement_characters(template_key):
    """A "?" in the output means a character silently failed to encode."""
    text = _pdf_text(_svc()._render_pdf(RESUME, TOKEN_SETS[template_key]))
    assert "?" not in text, f"{template_key}: unencodable character rendered as '?'"


def test_current_role_renders_as_present():
    text = _pdf_text(_svc()._render_pdf(RESUME, TOKEN_SETS["modern"]))
    assert "Present" in text


def test_plain_text_export_carries_the_dates():
    text = to_plain_text(RESUME)
    for label in _expected_labels():
        assert label in text


def test_docx_export_still_builds():
    blob = _svc()._render_docx(RESUME)
    assert blob[:2] == b"PK", "docx should be a zip container"
    assert len(blob) > 5000


# -------------------------------------------------- transliteration ----
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Mar 2021 \u2013 Present", "Mar 2021 - Present"),
        ("\u201cquoted\u201d", '"quoted"'),
        ("it\u2019s", "it's"),
        ("a \u2014 b", "a - b"),
        ("wait\u2026", "wait..."),
        ("10\u00d7 faster", "10x faster"),
        ("non\u00a0breaking", "non breaking"),
    ],
)
def test_word_typography_survives_the_pdf_encoder(raw, expected):
    """Resumes get pasted out of Word, which autocorrects quotes and dashes."""
    assert _latin1(raw) == expected


def test_transliteration_never_leaves_a_question_mark_for_common_input():
    sample = "Led \u201cProject X\u201d \u2014 cut costs 30%, scaled 10\u00d7\u2026 it\u2019s done"
    assert "?" not in _latin1(sample)


def test_smart_quotes_in_a_bullet_do_not_become_question_marks():
    data = migrate({
        **RESUME,
        "experience": [{
            "company": "Acme", "role": "SRE", "start_date": "2021",
            "current": True,
            "bullets": ["Shipped \u201cZero Downtime\u201d \u2014 saved 30% \u2026 it\u2019s live"],
        }],
    })
    text = _pdf_text(_svc()._render_pdf(data, TOKEN_SETS["modern"]))
    assert "?" not in text
    assert "Zero Downtime" in text


# -------------------------------------------------------- extras exports ---

_EXTRAS_DOC = {
    "contact": {"name": "Priya Sharma", "email": "p@example.com"},
    "summary": {"text": "Engineer."},
    "experience": [],
    "extras": [
        {"kind": "certifications", "title": "Certifications",
         "entries": [{"primary": "AWS Solutions Architect",
                      "secondary": "Amazon", "date": "2023", "detail": ""}]},
        {"kind": "custom", "title": "Volunteering", "entries": []},
    ],
}


def _export(client, user, fmt, doc):
    rid = new_resume(client, user, "Extras export", doc)
    r = client.get(f"/api/resumes/{rid}/export?format={fmt}",
                   headers={"X-User-Id": user})
    assert r.status_code == 200, r.text
    return r.content


def test_txt_export_includes_extras_and_omits_the_empty_section(client, priya):
    body = _export(client, priya, "txt", _EXTRAS_DOC).decode("utf-8")
    assert "CERTIFICATIONS" in body
    assert "AWS Solutions Architect" in body
    assert "Amazon" in body and "2023" in body
    assert "VOLUNTEERING" not in body, "an empty section must not print a heading"


def test_docx_export_includes_extras(client, priya):
    import io

    import docx

    document = docx.Document(io.BytesIO(_export(client, priya, "docx", _EXTRAS_DOC)))
    text = "\n".join(p.text for p in document.paragraphs)
    assert "Certifications" in text
    assert "AWS Solutions Architect" in text
    assert "Volunteering" not in text


def test_pdf_export_succeeds_and_grows_with_extras(client, priya):
    """fpdf2 output is not greppable, so assert the bytes respond to content."""
    without = dict(_EXTRAS_DOC, extras=[])
    small = _export(client, priya, "pdf", without)
    large = _export(client, priya, "pdf", _EXTRAS_DOC)
    assert small[:4] == b"%PDF" and large[:4] == b"%PDF"
    assert len(large) > len(small)


def test_a_resume_with_no_extras_key_still_exports(client, priya):
    legacy = {"contact": {"name": "Legacy"}, "summary": {"text": "x"},
              "experience": []}
    for fmt in ("txt", "docx", "pdf"):
        assert _export(client, priya, fmt, legacy)
