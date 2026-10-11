"""Profile links: harvesting, labelling, and the TS/Python parity pin.

The bug these cover: a resume whose contact line reads

    Pune, India • +91 8208300420 • me@gmail.com • LinkedIn • GitHub

has no URL in its *text* at all. The URLs live only in the PDF's annotation
layer, which `extract_text` discarded. `contact.links` parsed empty,
`contact.links.missing` fired, and the analysis docked two points for profile
links that were sitting in the uploaded file.
"""

import re
from pathlib import Path

import pytest

from app.services.export_service import _add_hyperlink
from app.services.parsing import extract_links, merge_extracted_links, structure_text
from app.services.resume_ops import (
    PROFILE_HOSTS,
    _known_host,
    label_links,
    profile_label,
    to_plain_text,
)

_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "links.ts"


# ----------------------------------------------------------------- labelling
@pytest.mark.parametrize(
    "url,label",
    [
        ("https://www.linkedin.com/in/tejas-taradgaonkar", "LinkedIn"),
        ("https://github.com/taradgaonkartejas", "GitHub"),
        ("http://GitHub.com/Someone", "GitHub"),
        # Subdomains are the same service.
        ("https://gist.github.com/x/1", "GitHub"),
        ("https://in.linkedin.com/in/x", "LinkedIn"),
        # Unknown host degrades to the bare domain, never a raw URL.
        ("https://tejas.dev/portfolio", "tejas.dev"),
        ("https://www.example.co.uk/a/b", "example.co.uk"),
    ],
)
def test_a_url_becomes_the_name_of_the_service(url, label):
    assert profile_label(url) == label


def test_a_lookalike_domain_is_not_the_real_one():
    """A plain suffix match would make `github.com.evil.ru` read as GitHub."""
    assert _known_host("https://github.com.phishing.ru/x") is None
    assert profile_label("https://github.com.phishing.ru/x") == "github.com.phishing.ru"


def test_two_links_to_the_same_service_stay_distinguishable():
    """Two runs both labelled "GitHub" would be indistinguishable."""
    pairs = label_links(
        ["https://github.com/alpha", "https://github.com/beta"]
    )
    assert [p[1] for p in pairs] == ["GitHub (alpha)", "GitHub (beta)"]


def test_a_single_link_is_not_disambiguated():
    assert label_links(["https://github.com/alpha"])[0][1] == "GitHub"


# ------------------------------------------------------- harvesting from PDF
def _pdf_with_links() -> bytes:
    """A one-page PDF whose links exist ONLY as annotations, as in the real file."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", "", 11)
    pdf.cell(0, 8, "JANE DOE", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(30, 8, "Pune, India | ")
    pdf.cell(20, 8, "LinkedIn", link="https://www.linkedin.com/in/jane")
    pdf.cell(10, 8, " | ")
    pdf.cell(20, 8, "GitHub", link="https://github.com/jane")
    pdf.ln(8)
    # A hyperlinked project title: must NOT become a contact link.
    pdf.cell(0, 8, "PROJECTS", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(40, 8, "Ledger service", link="https://acme-internal.example.com/x")
    return bytes(pdf.output())


def test_links_are_recovered_from_the_annotation_layer():
    found = extract_links("resume.pdf", _pdf_with_links())
    assert found["links"] == [
        "https://www.linkedin.com/in/jane",
        "https://github.com/jane",
    ]


def test_a_hyperlinked_project_does_not_become_a_profile_link():
    """Some templates link every project title; those belong to the project."""
    found = extract_links("resume.pdf", _pdf_with_links())
    assert all("acme-internal" not in u for u in found["links"])


def test_a_non_pdf_is_skipped_without_raising():
    assert extract_links("resume.docx", b"not a pdf") == {
        "links": [], "email": "", "phone": ""
    }


def test_a_corrupt_pdf_loses_links_but_never_fails_the_parse():
    """Links are a bonus. A broken annotation layer must not fail an upload."""
    assert extract_links("resume.pdf", b"%PDF-1.4 truncated")["links"] == []


def test_merge_keeps_text_layer_links_first_and_never_duplicates():
    data = {"contact": {"links": ["https://github.com/jane"], "email": "", "phone": ""}}
    merged = merge_extracted_links(
        data,
        {
            # Same link, trailing slash and different case.
            "links": ["https://GitHub.com/jane", "https://www.linkedin.com/in/jane"],
            "email": "jane@x.com",
            "phone": "",
        },
    )
    assert merged["contact"]["links"] == [
        "https://github.com/jane",
        "https://www.linkedin.com/in/jane",
    ]
    # mailto backfills an email the text layer missed, and is never a link.
    assert merged["contact"]["email"] == "jane@x.com"


def test_an_email_already_found_is_not_overwritten_by_a_mailto():
    data = {"contact": {"links": [], "email": "real@x.com", "phone": ""}}
    merged = merge_extracted_links(data, {"links": [], "email": "old@x.com", "phone": ""})
    assert merged["contact"]["email"] == "real@x.com"


# --------------------------------------------------------- contact location
def test_a_city_on_the_contact_line_is_extracted():
    """`contact.location` had NO extraction at all, so every resume was
    docked for a missing location regardless of what it said."""
    data = structure_text(
        "JANE DOE\nSan Francisco, CA | jane@x.com | 555-123-4567\n\nSUMMARY\nEngineer.\n"
    )
    assert data["contact"]["location"] == "San Francisco, CA"


def test_a_bullet_separated_contact_line_also_yields_a_location():
    data = structure_text(
        "TEJAS SURAJ TARADGAONKAR\n"
        "Pune, India • +91 8208300420 • t@gmail.com • LinkedIn • GitHub\n\n"
        "SUMMARY\nEngineer.\n"
    )
    assert data["contact"]["location"] == "Pune, India"


def test_an_anchor_word_is_not_mistaken_for_a_place():
    """"LinkedIn" and "GitHub" sit on the contact line as bare words."""
    data = structure_text(
        "JANE DOE\nLinkedIn • GitHub • jane@x.com • 555-123-4567\n\nSUMMARY\nEngineer.\n"
    )
    assert data["contact"]["location"] == ""


# --------------------------------------------------------------- the exports
def test_plain_text_carries_the_labels():
    text = to_plain_text(
        {
            "contact": {
                "name": "Jane", "email": "j@x.com", "phone": "", "location": "Pune",
                "links": ["https://www.linkedin.com/in/jane", "https://github.com/jane"],
            }
        }
    )
    assert "LinkedIn · GitHub" in text
    # Chosen deliberately: plain text cannot hyperlink, so only labels survive.
    assert "https://" not in text


def test_docx_hyperlink_builds_a_real_external_relationship():
    import docx

    document = docx.Document()
    para = document.add_paragraph()
    _add_hyperlink(para, "https://github.com/jane", "GitHub")
    xml = para._p.xml
    assert "w:hyperlink" in xml
    assert "GitHub" in xml
    assert any(
        "github.com/jane" in r._target
        for r in document.part.rels.values()
        if r.is_external
    )


# ----------------------------------------------- the TS/Python parity pin
def test_the_typescript_table_matches_the_python_one():
    """Two copies of a lookup table drift. A host added to one and not the
    other would render a label in the PDF and a bare domain in the preview."""
    src = _TS.read_text(encoding="utf-8")
    block = src.split("PROFILE_HOSTS: Record<string, string> = {", 1)[1].split("};", 1)[0]
    ts = dict(re.findall(r'"([^"]+)":\s*"([^"]+)"', block))
    assert ts == PROFILE_HOSTS, (
        "frontend/src/lib/links.ts and resume_ops.PROFILE_HOSTS disagree: "
        f"only in TS={set(ts) - set(PROFILE_HOSTS)}, "
        f"only in Python={set(PROFILE_HOSTS) - set(ts)}"
    )
