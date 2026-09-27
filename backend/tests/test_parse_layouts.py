"""Run every layout in the corpus through structure_text.

See tests/layouts.py for why the corpus exists.
"""

import pytest

from app.services.parsing import structure_text
from tests.layouts import LAYOUTS

IDS = [c["name"] for c in LAYOUTS]


@pytest.mark.parametrize("case", LAYOUTS, ids=IDS)
def test_layout_entry_count(case):
    got = structure_text(case["text"])["experience"]
    assert len(got) == len(case["experience"]), (
        f"{case['name']}: expected {len(case['experience'])} experience "
        f"entries, got {len(got)} -> {[e.get('company') for e in got]}"
    )


@pytest.mark.parametrize("case", LAYOUTS, ids=IDS)
def test_layout_company_and_role(case):
    got = structure_text(case["text"])["experience"]
    for i, want in enumerate(case["experience"]):
        assert i < len(got), f"{case['name']}: missing entry {i}"
        assert got[i]["company"] == want["company"], f"{case['name']} entry {i} company"
        assert got[i]["role"] == want["role"], f"{case['name']} entry {i} role"


@pytest.mark.parametrize("case", LAYOUTS, ids=IDS)
def test_layout_dates_captured(case):
    got = structure_text(case["text"])["experience"]
    for i, want in enumerate(case["experience"]):
        assert i < len(got), f"{case['name']}: missing entry {i}"
        entry = got[i]
        has = bool(
            (entry.get("dates") or "").strip()
            or (entry.get("raw_dates") or "").strip()
            or (entry.get("start_date") or "").strip()
        )
        assert has is want["has_dates"], f"{case['name']} entry {i} dates -> {entry}"


@pytest.mark.parametrize("case", LAYOUTS, ids=IDS)
def test_layout_bullets(case):
    got = structure_text(case["text"])["experience"]
    for i, want in enumerate(case["experience"]):
        assert i < len(got), f"{case['name']}: missing entry {i}"
        assert got[i]["bullets"] == want["bullets"], f"{case['name']} entry {i} bullets"


@pytest.mark.parametrize(
    "case", [c for c in LAYOUTS if "min_skills" in c], ids=lambda c: c["name"]
)
def test_layout_other_sections(case):
    data = structure_text(case["text"])
    assert len(data["skills"]) >= case["min_skills"]
    assert len(data["education"]) >= case["min_education"]
    assert case["summary_contains"] in data["summary"]["text"]


def test_no_fabricated_company_looks_like_prose():
    """A company name should never be a sentence fragment.

    This is the signature of the wrapped-bullet bug: the continuation line
    becomes an employer called "deploy time by 60% and improving...".
    """
    for case in LAYOUTS:
        for entry in structure_text(case["text"])["experience"]:
            company = entry.get("company", "")
            assert len(company.split()) <= 6, (
                f"{case['name']}: '{company}' looks like prose, not a company"
            )
            assert not company[:1].islower(), (
                f"{case['name']}: company '{company}' starts lowercase"
            )
