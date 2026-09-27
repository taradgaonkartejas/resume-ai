"""Splitting free-text `dates` into start/end/current.

The governing rule is **never guess**. A resume that silently reports the
wrong employment dates is far worse than one that shows an odd-looking
string, so anything ambiguous is preserved verbatim in `raw_dates`.
"""

import pytest

from app.services import resume_ops as ops


# ---------------------------------------------------------------- split ---
@pytest.mark.parametrize(
    "raw,start,end,current",
    [
        ("Jan 2020 – Present", "Jan 2020", "", True),
        ("Jan 2020 - Present", "Jan 2020", "", True),
        ("Jan 2020 — Present", "Jan 2020", "", True),
        ("March 2018 to June 2021", "March 2018", "June 2021", False),
        ("2019 – 2022", "2019", "2022", False),
        ("2019-2022", "2019", "2022", False),
        ("2019–present", "2019", "", True),
        ("Sep 2021 until Dec 2023", "Sep 2021", "Dec 2023", False),
        ("01/2020 – 06/2022", "01/2020", "06/2022", False),
        ("Present", "", "", True),
        ("current", "", "", True),
        ("", "", "", False),
    ],
)
def test_confident_splits(raw, start, end, current):
    got = ops.split_dates(raw)
    assert got == {"start_date": start, "end_date": end, "current": current}


@pytest.mark.parametrize(
    "raw",
    [
        "Jan-2020",            # a single month, not a range
        "Summer 2020",
        "2020",
        "Various dates",
        "see attached",
        "Q1 2020 onwards",
    ],
)
def test_ambiguous_strings_are_refused_rather_than_guessed(raw):
    assert ops.split_dates(raw) is None, f"{raw!r} should not be split"


def test_a_bare_year_is_not_torn_into_a_range():
    """"2020" is a single point in time; inventing an end date would be a lie."""
    assert ops.split_dates("2020") is None


# ------------------------------------------------------------ normalise ---
def test_legacy_entry_is_upcast():
    entry = ops.normalise_entry({"company": "Acme", "dates": "Jan 2020 – Present"})
    assert entry["start_date"] == "Jan 2020"
    assert entry["end_date"] == ""
    assert entry["current"] is True
    assert "raw_dates" not in entry


def test_ambiguous_legacy_entry_keeps_the_original_string():
    entry = ops.normalise_entry({"company": "Acme", "dates": "Various dates"})
    assert entry["raw_dates"] == "Various dates"
    assert entry["start_date"] == ""
    assert entry["current"] is False
    # And it still renders as the user typed it.
    assert ops.date_label(entry) == "Various dates"


def test_structured_fields_win_over_a_stale_legacy_string():
    entry = ops.normalise_entry({
        "company": "Acme", "dates": "OLD STALE VALUE",
        "start_date": "Feb 2021", "end_date": "Mar 2024",
    })
    assert entry["dates"] == "Feb 2021 – Mar 2024"


def test_normalise_is_idempotent():
    once = ops.normalise_entry({"dates": "Jan 2020 – Present"})
    twice = ops.normalise_entry(once)
    assert once == twice


def test_migrate_is_idempotent():
    data = {
        "contact": {"name": "Sam"},
        "experience": [{"company": "Acme", "dates": "2019 – 2022", "bullets": []}],
        "education": [{"school": "MIT", "dates": "2014-2018"}],
    }
    once = ops.migrate(data)
    twice = ops.migrate(once)
    assert once == twice


def test_migrate_fills_missing_top_level_sections():
    out = ops.migrate({"contact": {"name": "Sam"}})
    for key in ("summary", "experience", "projects", "education", "skills"):
        assert key in out
    assert out["contact"]["email"] == ""


def test_migrate_does_not_mutate_its_input():
    data = {"experience": [{"company": "Acme", "dates": "2019 – 2022"}]}
    before = str(data)
    ops.migrate(data)
    assert str(data) == before


def test_migrate_tolerates_a_string_summary():
    out = ops.migrate({"summary": "Just a string"})
    assert out["summary"] == {"text": "Just a string"}


# ---------------------------------------------------------------- label ---
@pytest.mark.parametrize(
    "entry,expected",
    [
        ({"start_date": "Jan 2020", "current": True}, "Jan 2020 – Present"),
        ({"start_date": "Jan 2020", "end_date": "Feb 2022"}, "Jan 2020 – Feb 2022"),
        ({"start_date": "2020"}, "2020"),
        ({"end_date": "2022"}, "2022"),
        ({"current": True}, "Present"),
        ({"raw_dates": "Various dates"}, "Various dates"),
        ({"dates": "legacy only"}, "legacy only"),
        ({}, ""),
    ],
)
def test_date_label(entry, expected):
    assert ops.date_label(entry) == expected


def test_current_overrides_a_leftover_end_date():
    """Ticking "I work here now" must not leave the old end date showing."""
    label = ops.date_label({"start_date": "Jan 2020", "end_date": "Feb 2022",
                            "current": True})
    assert label == "Jan 2020 – Present"


def test_label_never_disagrees_with_the_mirror():
    """The derived `dates` key and the accessor cannot drift apart."""
    for raw in ["Jan 2020 – Present", "2019-2022", "Various dates", ""]:
        entry = ops.normalise_entry({"dates": raw})
        assert entry["dates"] == ops.date_label(entry)
