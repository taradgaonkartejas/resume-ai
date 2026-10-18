"""`edu_N.<field>` addressing.

Until now `resume_ops` understood four ref forms -- summary.text,
exp_N.bullet_M, prj_N.bullet_M, skills.N.items -- and there was no way to
address an education entry at all. That, not the model, was the blocker on
education suggestions: one could be generated, but never persisted,
accepted or undone.

Scope note: this adds the ADDRESSING. Nothing generates education
suggestions yet, and pointing the tailor writer at education is a separate
decision -- "add Kubernetes to your degree" is keyword stuffing, and a
degree is a credential rather than prose.
"""

import pytest

from app.services import resume_ops
from app.services.exceptions import InvalidTargetRef


def _data():
    return {
        "contact": {"name": "A", "email": "a@b.c", "phone": "1", "location": "X", "links": []},
        "summary": {"text": "S"},
        "experience": [],
        "projects": [],
        "skills": [],
        "education": [
            {
                "school": "Pune University",
                "degree": "B.E.",
                "field_of_study": "Computer Science",
                "location": "Pune",
                "grade": "73%",
                "dates": "2016-2020",
            },
            {"school": "Second School", "degree": "Diploma"},
        ],
    }


@pytest.mark.parametrize(
    "field,expected",
    [
        ("school", "Pune University"),
        ("degree", "B.E."),
        ("field_of_study", "Computer Science"),
        ("location", "Pune"),
        ("grade", "73%"),
    ],
)
def test_every_addressable_field_resolves(field, expected):
    assert resume_ops.resolve(_data(), f"edu_0.{field}") == expected


def test_a_second_entry_is_addressable_by_index():
    assert resume_ops.resolve(_data(), "edu_1.school") == "Second School"


def test_a_missing_field_resolves_to_empty_not_an_error():
    """Absent is not invalid.

    The second entry has no grade. The ref is still well-formed and the
    field is still addressable -- a suggestion that FILLS it is exactly the
    useful case, and raising here would make it unreachable.
    """
    assert resume_ops.resolve(_data(), "edu_1.grade") == ""


@pytest.mark.parametrize("field", ["dates", "start_date", "end_date", "current"])
def test_date_fields_are_not_addressable(field):
    """Stored twice -- a display string and a structured pair.

    Patching one without the other desynchronises them, and a date is a
    fact rather than prose, so there is nothing for a writer to improve.
    """
    with pytest.raises(InvalidTargetRef):
        resume_ops.resolve(_data(), f"edu_0.{field}")


def test_an_unknown_field_names_the_allowed_ones():
    with pytest.raises(InvalidTargetRef) as err:
        resume_ops.resolve(_data(), "edu_0.favourite_colour")
    assert "school" in str(err.value) and "degree" in str(err.value)


def test_an_out_of_range_entry_is_rejected():
    with pytest.raises(InvalidTargetRef):
        resume_ops.resolve(_data(), "edu_9.school")


def test_exists_agrees_with_resolve():
    data = _data()
    assert resume_ops.exists(data, "edu_0.school")
    assert resume_ops.exists(data, "edu_1.grade")
    assert not resume_ops.exists(data, "edu_9.school")
    assert not resume_ops.exists(data, "edu_0.dates")


# ----------------------------------------------------------------- patching
def test_apply_patch_writes_the_field():
    patched = resume_ops.apply_patch(_data(), "edu_0.field_of_study", "Computer Engineering")
    assert patched["education"][0]["field_of_study"] == "Computer Engineering"


def test_apply_patch_does_not_mutate_the_input():
    data = _data()
    resume_ops.apply_patch(data, "edu_0.school", "Somewhere Else")
    assert data["education"][0]["school"] == "Pune University"


def test_apply_patch_leaves_siblings_alone():
    patched = resume_ops.apply_patch(_data(), "edu_0.grade", "81%")
    assert patched["education"][0]["degree"] == "B.E."
    assert patched["education"][1]["school"] == "Second School"


def test_apply_patch_refuses_a_date_field():
    with pytest.raises(InvalidTargetRef):
        resume_ops.apply_patch(_data(), "edu_0.dates", "2017-2021")


def test_apply_patch_refuses_an_out_of_range_entry():
    with pytest.raises(InvalidTargetRef):
        resume_ops.apply_patch(_data(), "edu_5.school", "Nowhere")


def test_a_patch_round_trips_through_resolve():
    """The two halves must agree, which is the bug the seed data shipped."""
    patched = resume_ops.apply_patch(_data(), "edu_0.grade", "81%")
    assert resume_ops.resolve(patched, "edu_0.grade") == "81%"


def test_every_addressable_field_is_actually_rendered():
    """Addressing a field the output drops would be a silent no-op.

    `education_lines` is the one formatter the preview and all three
    exporters share, so if a patched field never reaches it the user
    accepts a change that appears nowhere.
    """
    data = _data()
    for field in resume_ops._EDU_FIELDS:
        sentinel = "ZZSENTINELZZ"
        patched = resume_ops.apply_patch(data, f"edu_0.{field}", sentinel)
        rendered = " ".join(resume_ops.education_lines(patched["education"][0]))
        assert sentinel in rendered, (
            f"edu_0.{field} is addressable but education_lines() drops it, so "
            "accepting a suggestion for it would change nothing visible"
        )
