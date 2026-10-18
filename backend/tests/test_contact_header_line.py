"""The name/email/phone-on-one-line header.

The defect, reproduced from a real upload: a header that runs the name,
email and phone together on a single line lost the name entirely AND filed
the name as the location.

    'Tejas T | taradgaonkartejas@gmail.com | +91 8208300420'
        -> contact.name == ''
        -> contact.location == 'Tejas T'

Two failures from one cause. The name branch rejected any first line
containing "@", so nothing claimed the name; the location sweep then skips
"any field equal to the name", and with the name empty the person's name was
the first unclaimed field on the line. The wrong value reaches the DOCX and
PDF exports, so a resume went to an employer with a name printed as a city
and no name at all.

These tests pin BOTH halves, plus the headers that must keep working: a name
on its own line, and -- the reason the fix reads only the FIRST field -- a
combined header with no name in it at all, where a real city must stay a
city rather than being promoted to the name.
"""

import pytest

from app.services.parsing import _looks_like_person_name, structure_text

_BODY = "SUMMARY\nEngineer with five years of experience building services.\n"


def _contact(header: str) -> dict:
    return structure_text(header + "\n" + _BODY)["contact"]


# (id, header line(s), expected name, expected location)
COMBINED_HEADERS = [
    (
        "pipe separated",
        "Tejas T | taradgaonkartejas@gmail.com | +91 8208300420",
        "Tejas T",
        "",
    ),
    (
        "bullet separated with a city",
        "Jane Doe • jane.doe@example.com • (415) 555-0132 • San Francisco, CA",
        "Jane Doe",
        "San Francisco, CA",
    ),
    (
        "wide whitespace separated",
        "Priya Sharma    priya@example.com    +91 98765 43210    Pune, India",
        "Priya Sharma",
        "Pune, India",
    ),
    (
        "en dash separated",
        "Alex Kim – alex.kim@example.com – +1 206 555 0188",
        "Alex Kim",
        "",
    ),
    (
        "name and email only",
        "Sam Rivera | sam@example.com",
        "Sam Rivera",
        "",
    ),
    (
        "phone but no email on the line",
        "Rohit Nair | +91 98765 43210 | Bengaluru",
        "Rohit Nair",
        "Bengaluru",
    ),
]


@pytest.mark.parametrize(
    "header,name,location",
    [c[1:] for c in COMBINED_HEADERS],
    ids=[c[0] for c in COMBINED_HEADERS],
)
def test_a_combined_header_yields_the_name_and_the_location(header, name, location):
    contact = _contact(header)
    assert contact["name"] == name
    assert contact["location"] == location


@pytest.mark.parametrize(
    "header,name,_location",
    [c[1:] for c in COMBINED_HEADERS],
    ids=[c[0] for c in COMBINED_HEADERS],
)
def test_the_name_is_never_filed_as_the_location(header, name, _location):
    """The specific regression: the name must not leak into `location`."""
    contact = _contact(header)
    assert contact["location"] != name
    assert contact["location"] != contact["name"]


def test_a_name_on_its_own_line_still_wins():
    """The common layout must be untouched by the combined-header rescue."""
    contact = _contact(
        "Tejas T\ntaradgaonkartejas@gmail.com | +91 8208300420 | Pune, India"
    )
    assert contact["name"] == "Tejas T"
    assert contact["location"] == "Pune, India"
    assert contact["email"] == "taradgaonkartejas@gmail.com"


def test_a_combined_header_with_no_name_keeps_the_city_as_the_city():
    """Why only the FIRST field is considered as a name.

    Scanning the whole line for something "name shaped" would promote
    'Pune, India' to the name here and lose the location -- a worse result
    than an empty name, which the contact finding already prompts for.
    """
    contact = _contact("contact@example.com | +91 98765 43210 | Pune, India")
    assert contact["name"] == ""
    assert contact["location"] == "Pune, India"


def test_the_email_and_phone_are_never_mistaken_for_the_name():
    for header, *_ in COMBINED_HEADERS:
        contact = _contact(header)
        assert "@" not in contact["name"]
        assert not any(ch.isdigit() for ch in contact["name"])


def test_a_section_heading_on_the_contact_line_is_not_a_name():
    contact = _contact("EXPERIENCE | a@b.com | 555-123-4567")
    assert contact["name"] == ""


class TestLooksLikePersonName:
    """The predicate is a negative test -- it rejects what it can identify."""

    @pytest.mark.parametrize(
        "field",
        [
            "Tejas T",
            "Jane Doe",
            "TEJAS SURAJ TARADGAONKAR",
            "Sam",
            "Maria del Carmen Santos",
        ],
    )
    def test_accepts_names(self, field):
        assert _looks_like_person_name(field) is True

    @pytest.mark.parametrize(
        "field",
        [
            "jane.doe@example.com",  # email
            "+91 8208300420",  # phone
            "(415) 555-0132",  # phone, bracketed
            "github.com/taradgaonkartejas",  # link
            "linkedin",  # bare anchor word
            "2024",  # digits only
            "221B Baker Street",  # address: contains digits
            "",  # empty
            "A",  # too short
            "EXPERIENCE",  # section heading
            "a name that is far too long to be anybody real " * 2,  # too long
        ],
    )
    def test_rejects_everything_else(self, field):
        assert _looks_like_person_name(field) is False


# ---------------------------------------------------------------------------
# Round 2. The first fix only read the FIRST field of the contact line, which
# left the reported symptom intact whenever the email came first, and two
# separators were not separators at all.
# ---------------------------------------------------------------------------

SEPARATORS = [
    ("pipe", "Tejas T | tejas@example.com | +91 8208300420"),
    ("comma", "Tejas T, tejas@example.com, +91 8208300420"),
    ("single space", "Tejas T tejas@example.com +91 8208300420"),
    ("tab", "Tejas T\ttejas@example.com\t+91 8208300420"),
    ("spaced slash", "Tejas T / tejas@example.com / +91 8208300420"),
    ("semicolon", "Tejas T; tejas@example.com; +91 8208300420"),
    ("middle dot", "Tejas T \u00b7 tejas@example.com \u00b7 +91 8208300420"),
    ("en dash", "Tejas T \u2013 tejas@example.com \u2013 +91 8208300420"),
    ("no padding", "Tejas T|tejas@example.com|+91 8208300420"),
]


@pytest.mark.parametrize(
    "header", [s[1] for s in SEPARATORS], ids=[s[0] for s in SEPARATORS]
)
def test_the_name_survives_every_separator(header):
    """`/` and `;` were not in the separator set, so the name came back as
    "Tejas T / /" -- the contact tokens were stripped out and the dividers
    were left behind."""
    contact = _contact(header)
    assert contact["name"] == "Tejas T"
    assert contact["location"] != "Tejas T"


def test_a_spaced_slash_separates_but_a_bare_slash_does_not():
    """A bare `/` would split "github.com/tejas" into host and username,
    and the username would then look exactly like an unclaimed name."""
    contact = _contact("Tejas T | tejas@example.com | github.com/taradgaonkartejas")
    assert contact["name"] == "Tejas T"
    assert contact["location"] == ""
    assert "taradgaonkartejas" not in contact["location"]


def test_the_name_is_found_when_the_email_comes_first():
    """THE REPORTED BUG, round 2.

    Reading only field 0 meant an email-led header still produced an empty
    name and the person's name filed as the city.
    """
    contact = _contact("tejas@example.com | Tejas T | +91 8208300420")
    assert contact["name"] == "Tejas T"
    assert contact["location"] == ""


def test_the_name_is_found_between_two_contact_fields():
    contact = _contact("+91 8208300420 | Tejas T | tejas@example.com")
    assert contact["name"] == "Tejas T"


def test_a_field_trailing_all_contact_data_is_the_location():
    """The positional rule, stated as a test.

    Identity first, contact details next, city last. A field with more
    contact data after it is the name; a field that trails all of it is the
    location.
    """
    contact = _contact("tejas@example.com | +91 8208300420 | Pune, India")
    assert contact["name"] == ""
    assert contact["location"] == "Pune, India"


def test_a_section_heading_on_the_contact_line_is_not_the_location_either():
    """`_classify` runs on the whole line, which misses a heading that is
    only one field of it -- so it was rejected as a name and then accepted
    as the city."""
    contact = _contact("EXPERIENCE | a@b.com | 555-123-4567")
    assert contact["name"] == ""
    assert contact["location"] == ""


def test_known_limitation_a_trailing_name_is_read_as_a_location():
    """Documented, not fixed, because it is genuinely undecidable.

    "a@b.com | +91 ... | Tejas T" and "a@b.com | +91 ... | Pune, India" are
    the same shape. Separating them needs a name or city lexicon; guessing
    "name" would put every city in the name box, which is the worse error
    and is the far more common header. Headers essentially never put the
    name last.
    """
    contact = _contact("a@b.com | +91 8208300420 | Tejas T")
    assert contact["name"] == ""
    assert contact["location"] == "Tejas T"   # <- the limitation
