"""ATS parse-safety: content checks, and a round-trip gate on the exporter.

The round-trip is the part that matters. Most ATS checkers score a resume
against a folklore checklist; we hold both the structured data and the
renderer, so we can measure instead:

    structured_data -> render(template) -> extract_text -> structure_text

Anything that does not survive that loop is something a real parser can lose.
It caught two live defects in our own templates (see the docstrings below).

Honest about its limits: our extractor is pdfplumber plus our own segmenter,
not Workday's. A clean round-trip is necessary, not sufficient.
"""

import io

import pdfplumber
import pytest

from app.services import export_service as E
from app.services.ats import (
    decorative_glyphs,
    out_of_order_experience,
    unparseable_dates,
)
from app.services.parsing import extract_text, structure_text

# --------------------------------------------------------------- unit checks


def test_arrows_and_emoji_are_flagged_but_ordinary_bullets_are_not():
    """Flagging "•" or an en dash would be folklore: they are on every resume
    and survive every parser. Arrows and emoji genuinely do not."""
    doc = {
        "contact": {"name": "J", "email": "", "phone": "", "location": "", "links": []},
        "experience": [{"company": "A", "role": "R", "dates": "2020",
                        "bullets": ["Cut latency 40% → 12ms ✅", "• Normal bullet – fine"]}],
    }
    assert decorative_glyphs(doc) == ["→", "✅"]

    clean = {
        "contact": {"name": "J", "email": "", "phone": "", "location": "", "links": []},
        "experience": [{"company": "A", "role": "R", "dates": "2020",
                        "bullets": ["• Shipped the thing – on time, 40% faster"]}],
    }
    assert decorative_glyphs(clean) == []


@pytest.mark.parametrize(
    "dates,ok",
    [
        ("Jan 2024 – Mar 2026", True),
        ("January 2020 - Present", True),
        ("2020 – 2024", True),
        ("2024", True),
        ("03/2024", True),
        # The forms the research names as breaking Workday and Taleo.
        ("Recent", False),
        ("summer of 2024", False),
    ],
)
def test_dates_an_ats_cannot_key_on_are_flagged(dates, ok):
    doc = {"experience": [{"company": "A", "role": "R", "dates": dates, "bullets": []}]}
    assert (unparseable_dates(doc) == []) is ok, dates


def test_a_missing_date_is_not_a_format_complaint():
    """Absence is a different finding; do not report it twice."""
    doc = {"experience": [{"company": "A", "role": "R", "dates": "", "bullets": []}]}
    assert unparseable_dates(doc) == []


def test_experience_out_of_order_is_detected():
    older_first = {"experience": [
        {"company": "A", "role": "R", "end_date": "2019", "bullets": []},
        {"company": "B", "role": "R", "end_date": "2024", "bullets": []},
    ]}
    assert out_of_order_experience(older_first) is True

    newest_first = {"experience": [
        {"company": "B", "role": "R", "current": True, "bullets": []},
        {"company": "A", "role": "R", "end_date": "2019", "bullets": []},
    ]}
    assert out_of_order_experience(newest_first) is False


def test_a_single_dated_role_is_never_out_of_order():
    doc = {"experience": [{"company": "A", "role": "R", "end_date": "2024", "bullets": []}]}
    assert out_of_order_experience(doc) is False


# ------------------------------------------------------- round-trip fidelity

DOC = {
    "contact": {
        "name": "Jane Doe", "headline": "Senior Engineer",
        "email": "jane@example.com", "phone": "+91 8208300420",
        "location": "Pune, India", "links": ["https://github.com/jane"],
    },
    "summary": {"text": "Backend engineer with eight years on payment systems."},
    "experience": [{
        "company": "Acme Corp", "role": "Staff Engineer", "location": "Pune",
        "dates": "Jan 2020 - Present", "current": True,
        "bullets": ["Cut p95 latency by 40% across the payments tier."],
    }],
    "education": [{
        "school": "Vishwakarma Institute of Technology", "degree": "B.E.",
        "location": "Pune", "grade": "CGPA 7.79", "dates": "2016",
    }],
    "skills": [{"label": "Languages",
                "items": ["Python", "Java", "Go", "Rust", "TypeScript", "SQL"]}],
    "projects": [],
}


def _round_trip(tokens: dict) -> dict:
    svc = E.ExportService.__new__(E.ExportService)
    pdf = svc._render_pdf(DOC, tokens)
    with pdfplumber.open(io.BytesIO(pdf)) as f:
        assert f.pages[0].extract_text(), "no text layer at all"
    return structure_text(extract_text("r.pdf", pdf))


def _templates() -> list[tuple[str, dict]]:
    """Every seeded template, exercised by key."""
    from app.services import seed

    out = []
    for key, _name, _desc, tok in seed.TEMPLATES:
        tokens = dict(E.DEFAULT_TOKENS)
        tokens.update(tok or {})
        out.append((key, tokens))
    return out or [("default", dict(E.DEFAULT_TOKENS))]


@pytest.mark.parametrize("key,tokens", _templates(), ids=lambda v: v if isinstance(v, str) else "")
def test_contact_details_survive_the_export_for_every_template(key, tokens):
    """The defect this exists to prevent, found by running it:

    `header: "split"` rendered the name on the left and the contact block on
    the right, starting at the same y. A PDF text extractor reads straight
    across the page, so it came back as

        Jane Doe jane@example.com
        +91 8208300420
        Senior Engineer Pune, India

    -- the name fused to the email, the job title to the city. That is the
    multi-column scramble (~34% of all ATS field errors) landing on the one
    block whose corruption means a recruiter cannot make contact at all.
    Two of the seven shipped templates did this.
    """
    got = _round_trip(tokens)["contact"]
    assert got["name"] == "Jane Doe", f"{key}: name came back as {got['name']!r}"
    assert got["email"] == "jane@example.com", f"{key}: {got['email']!r}"
    assert "8208300420" in (got["phone"] or ""), f"{key}: {got['phone']!r}"


@pytest.mark.parametrize("key,tokens", _templates(), ids=lambda v: v if isinstance(v, str) else "")
def test_one_job_stays_one_job_through_the_export(key, tokens):
    data = _round_trip(tokens)
    assert len(data["experience"]) == 1, (
        f"{key}: one job exported as {len(data['experience'])} "
        f"-> {[e['company'] for e in data['experience']]}"
    )


@pytest.mark.parametrize("key,tokens", _templates(), ids=lambda v: v if isinstance(v, str) else "")
def test_skills_survive_the_grid_layout(key, tokens):
    """Second defect found here: a multi-column skills grid extracts row-wise,
    so "- Python  - Go  - TypeScript" came back as one junk token run. Using
    commas as the separator makes the very same row read as a valid skills
    line while the grid still looks like a grid."""
    text = "\n".join(
        " ".join(g.get("items", [])) for g in _round_trip(tokens).get("skills", [])
    )
    for skill in ("Python", "Java", "Go", "Rust", "TypeScript", "SQL"):
        assert skill in text, f"{key}: lost {skill!r} -> {text!r}"


def test_the_exported_resume_is_ats_clean_by_our_own_checks():
    """Whatever we tell users to fix, our own output must already satisfy."""
    data = _round_trip(dict(E.DEFAULT_TOKENS))
    assert decorative_glyphs(data) == []
    assert unparseable_dates(data) == []
    assert out_of_order_experience(data) is False
