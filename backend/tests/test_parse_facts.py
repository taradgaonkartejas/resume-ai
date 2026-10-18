"""`parse_facts` — what we observed while reading the uploaded file.

The scorer grades content only, and deliberately so: layout is a property
of the template and is fixed in the exporter. But that reasoning covers
documents WE render. It says nothing about the file the user handed us,
which the exporter never saw and cannot repair.

Everything here is advisory. No scored finding, no change to the finding
ids, no change to any `Δscore == points` assertion.
"""

import io

import pytest

from app.services.parsing import collect_parse_facts, parse_advisories


def _pdf(text_lines, two_column=False, blank=False):
    """A small real PDF, built with reportlab."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    if blank:
        # A page with graphics but NO text layer -- what a scan looks like.
        # Drawing nothing at all makes reportlab emit a zero-page file,
        # which is a different (and unrealistic) thing entirely.
        c.rect(60, 60, 400, 600, fill=0)
    else:
        y = 800
        for line in text_lines:
            c.drawString(60, y, line)
            if two_column:
                c.drawString(330, y, line)
            y -= 16
    c.showPage()
    c.save()
    return buf.getvalue()


# ----------------------------------------------------------------- basics
def test_facts_record_the_format_and_size():
    content = _pdf(["Jane Doe", "Experience", "Acme Corp"])
    facts = collect_parse_facts("cv.pdf", content, "Jane Doe\nExperience\nAcme Corp")
    assert facts["format"] == "pdf"
    assert facts["chars"] > 0
    assert facts["pages"] == 1


def test_a_clean_single_column_resume_produces_no_advisory():
    """An advisory shown on every upload is an advisory nobody reads."""
    content = _pdf(["Jane Doe", "Experience", "Acme Corp", "Education", "Skills"])
    raw = "Jane Doe\nExperience\nAcme Corp\nEducation\nSkills"
    assert parse_advisories(collect_parse_facts("cv.pdf", content, raw)) == []


def test_facts_never_raise_on_a_corrupt_file():
    """Failing an upload to compute an advisory would be an absurd trade."""
    facts = collect_parse_facts("cv.pdf", b"this is not a pdf at all", "")
    assert isinstance(facts, dict)
    assert facts["format"] == "pdf"


def test_empty_facts_produce_no_advisories():
    assert parse_advisories({}) == []
    assert parse_advisories({"format": "pdf", "chars": 500}) == []


# ------------------------------------------------------------- image-only
def test_a_pdf_with_no_text_layer_is_flagged():
    """The most destructive failure: the recruiter receives nothing.

    No mainstream ATS runs OCR, so a scanned resume arrives as an empty
    profile and the candidate never learns why.
    """
    facts = collect_parse_facts("scan.pdf", _pdf([], blank=True), "")
    assert facts["image_only"] is True
    advisories = parse_advisories(facts)
    assert advisories and "scan" in advisories[0].lower()


def test_a_pdf_with_real_text_is_not_called_a_scan():
    content = _pdf(["Jane Doe", "Senior Engineer", "Experience", "Acme Corp 2020"])
    raw = "Jane Doe\nSenior Engineer\nExperience\nAcme Corp 2020"
    assert collect_parse_facts("cv.pdf", content, raw)["image_only"] is False


# ------------------------------------------------------------ two columns
def test_a_two_column_pdf_is_detected():
    # A real page's worth of text on both sides: the gutter detector wants
    # substantial text either side of the band, not three short lines.
    lines = [f"Shipped release {i} on time" for i in range(30)]
    content = _pdf(lines, two_column=True)
    raw = "\n".join(lines)
    facts = collect_parse_facts("cv.pdf", content, raw)
    assert facts["multi_column_pages"] >= 1
    assert any("two-column" in a for a in parse_advisories(facts))


def test_a_single_column_pdf_reports_no_columns():
    lines = [f"Shipped release {i} on time" for i in range(30)]
    content = _pdf(lines)
    raw = "\n".join(lines)
    assert collect_parse_facts("cv.pdf", content, raw)["multi_column_pages"] == 0


# ------------------------------------------------------- unknown headings
def test_an_unrecognised_heading_is_reported():
    """Taleo ignores content under a heading it does not know."""
    raw = (
        "Jane Doe\njane@x.com\nPune\n\nMy Journey\n\nAcme Corp\n\n"
        "Education\n\nPune University"
    )
    facts = collect_parse_facts("cv.txt", b"", raw)
    assert "My Journey" in facts["unknown_headings"]
    assert "Education" not in facts["unknown_headings"]


def test_the_candidates_own_name_is_not_called_a_heading():
    """An early version reported "Jane Doe" as an unrecognised section.

    Wrong and alarming: a name and a section label are the same shape.
    """
    raw = "Jane Doe\n\njane@x.com\n\nExperience\n\nAcme Corp\n\nBuilt things"
    facts = collect_parse_facts("cv.txt", b"", raw)
    assert "Jane Doe" not in facts["unknown_headings"]


def test_a_company_name_on_its_own_line_is_not_a_heading():
    raw = "Jane\nj@x.com\nPune\n\nExperience\n\nAcme Corp\nSenior Engineer\nBuilt it"
    assert "Acme Corp" not in collect_parse_facts("cv.txt", b"", raw)["unknown_headings"]


def test_known_headings_are_never_reported():
    raw = "Jane\nj@x.com\nPune\n\nExperience\n\nEducation\n\nSkills\n\nProjects\n"
    assert collect_parse_facts("cv.txt", b"", raw)["unknown_headings"] == []


def test_the_heading_list_is_capped():
    raw = "Jane\nj@x.com\nPune\n" + "".join(
        f"\nStrange Heading {chr(65 + i)}\n" for i in range(20)
    )
    assert len(collect_parse_facts("cv.txt", b"", raw)["unknown_headings"]) <= 6


# -------------------------------------------------------------- advisories
def test_advisories_are_ordered_worst_first():
    facts = {
        "image_only": True,
        "multi_column_pages": 2,
        "unknown_headings": ["My Journey"],
    }
    out = parse_advisories(facts)
    assert len(out) == 3
    assert "scan" in out[0].lower(), "an unreadable file outranks a layout note"


def test_the_advisory_names_the_heading_it_means():
    out = parse_advisories({"unknown_headings": ["My Journey"]})
    assert out and "My Journey" in out[0]


# ------------------------------------------------- the user's real resumes
REAL = [
    "/home/user/uploads/Tejas_Taradgaonkar_Resume_Java.pdf",
    "/home/user/uploads/Tejas_Taradgaonkar_Resume_react_java.pdf",
]


@pytest.mark.parametrize("path", REAL)
def test_the_real_resumes_are_inspected_without_error(path):
    """The two files that started this whole bug pass."""
    import pathlib

    p = pathlib.Path(path)
    if not p.exists():
        pytest.skip(f"{path} not present in this sandbox")
    content = p.read_bytes()
    from app.services.parsing import extract_text

    raw = extract_text(p.name, content)
    facts = collect_parse_facts(p.name, content, raw)
    assert facts["pages"] >= 1
    assert facts["chars"] > 500, "these are text PDFs, not scans"
    assert facts["image_only"] is False
    # Advisories may or may not fire; they must at least be well-formed.
    for line in parse_advisories(facts):
        assert isinstance(line, str) and line.strip()
