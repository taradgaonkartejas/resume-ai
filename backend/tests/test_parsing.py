"""Text -> structured resume, and the optional-sections parser.

`structure_text` is pure, so these are fast unit tests with no DB or client.
"""

import pytest

from app.services import parsing, resume_ops

# --------------------------------------------------------------- extras ---
# The parser is the only change in this feature that can make a resume that
# works TODAY parse worse. These tests exist to hold that line.

_WITH_EXTRAS = """Priya Sharma
Senior Backend Engineer
priya@example.com | +91 98765 43210 | Pune, India

SUMMARY
Backend engineer with six years building payment systems.

EXPERIENCE
Acme Payments
Senior Backend Engineer
Jan 2021 - Present
- Reduced checkout latency by 40% across 3 services
- Led migration of 12 microservices to Kubernetes

EDUCATION
B.Tech Computer Science, COEP, 2018

SKILLS
Python, FastAPI, PostgreSQL

CERTIFICATIONS
AWS Certified Solutions Architect, Amazon Web Services, 2023
Certified Kubernetes Administrator | CNCF | 2022

LANGUAGES
English - Fluent
Hindi - Native

VOLUNTEERING
Mentor at Django Girls Pune

AWARDS
Employee of the Year, Acme Payments, 2022
"""

# Byte-identical to _WITH_EXTRAS minus the four optional sections.
_NO_EXTRAS = _WITH_EXTRAS.split("CERTIFICATIONS")[0].rstrip() + "\n"


def _kinds(data):
    return [x["kind"] for x in data["extras"]]


def test_extra_sections_do_not_disturb_the_sections_that_already_worked():
    """THE regression guard: the core resume must parse identically with and
    without the optional sections appended."""
    with_x = parsing.structure_text(_WITH_EXTRAS)
    without = parsing.structure_text(_NO_EXTRAS)
    for key in ("contact", "summary", "experience", "education", "skills"):
        assert with_x[key] == without[key], f"{key} changed when extras appeared"
    assert without["extras"] == []


def test_known_headings_land_in_the_right_bucket():
    data = parsing.structure_text(_WITH_EXTRAS)
    assert _kinds(data) == ["certifications", "languages", "custom", "awards"]


def test_an_unrecognised_all_caps_heading_becomes_a_custom_section():
    data = parsing.structure_text(_WITH_EXTRAS)
    vol = next(x for x in data["extras"] if x["kind"] == "custom")
    assert vol["title"] == "Volunteering"
    assert vol["entries"][0]["primary"] == "Mentor at Django Girls Pune"


def test_a_line_splits_on_the_first_separator_and_lifts_a_trailing_year():
    data = parsing.structure_text(_WITH_EXTRAS)
    certs = next(x for x in data["extras"] if x["kind"] == "certifications")
    assert certs["entries"][0] == {
        "primary": "AWS Certified Solutions Architect",
        "secondary": "Amazon Web Services", "date": "2023", "detail": "",
    }
    assert certs["entries"][1]["secondary"] == "CNCF"
    langs = next(x for x in data["extras"] if x["kind"] == "languages")
    assert langs["entries"][0] == {
        "primary": "English", "secondary": "Fluent", "date": "", "detail": "",
    }


@pytest.mark.parametrize("line", [
    "Acme Corporation",          # title case -> a company, not a heading
    "Senior Backend Engineer",   # ditto, a role
    "- Built a thing",           # a bullet
    "priya@example.com",         # contact detail
    "Jan 2021 - Present",        # dates
    "AWS 2023",                  # contains a digit
    "This is a much longer sentence than any real section heading.",
])
def test_headings_that_must_never_be_promoted(line):
    assert parsing._custom_heading(line) is None


def test_the_name_at_the_top_is_never_promoted_to_a_section():
    """ALL-CAPS names are common. Detection is armed only after a real heading,
    which is what stops "PRIYA SHARMA" from becoming a section."""
    data = parsing.structure_text(
        "PRIYA SHARMA\npriya@example.com\n\nSUMMARY\nEngineer.\n"
    )
    assert data["extras"] == []
    # Verbatim, not title-cased -- that is existing behaviour and not this
    # feature's business. What matters here is that it stayed in `contact`.
    assert data["contact"]["name"] == "PRIYA SHARMA"


def test_a_heading_with_nothing_under_it_is_dropped():
    data = parsing.structure_text(
        "SUMMARY\nEngineer.\n\nEXPERIENCE\nAcme\n- Did work\n\nCERTIFICATIONS\n"
    )
    assert data["extras"] == []


def test_extras_survive_a_migrate_round_trip():
    data = parsing.structure_text(_WITH_EXTRAS)
    assert resume_ops.migrate(data)["extras"] == data["extras"]
