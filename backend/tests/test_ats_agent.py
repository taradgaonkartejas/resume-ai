"""The ATS agent: judgement calls, gated and guarded.

The deterministic checks live in test_ats.py. These tests cover the parts
that are specific to putting a model in the loop: that it is NOT called when
there is nothing to ask, that its answer is filtered down to what it was
actually asked about, and that it degrades to something honest without a key.
"""
from __future__ import annotations

import pytest

from app.ai import agents, graphs
from app.ai.schemas import AtsReview, SkillExpansion
from app.services import ats

CLEAN = {
    "contact": {"name": "Jane Doe", "email": "j@x.com"},
    "summary": {"text": "Backend engineer."},
    "experience": [
        {"company": "Acme", "role": "Staff Engineer", "dates": "Jan 2020 - Present",
         "bullets": ["Cut p95 latency by 40%."]}
    ],
    "education": [], "skills": [{"label": "", "items": ["Python", "SQL"]}], "extras": [],
}

MESSY = {
    **CLEAN,
    "skills": [{"label": "", "items": ["Python", "SQL", "PMP", "CKA"]}],
    "extras": [
        {
            "kind": "custom",
            "title": "My Journey",
            "entries": [
                {"primary": "Senior Engineer", "secondary": "Globex",
                 "date": "2018 - 2020", "detail": ""},
                {"primary": "Engineer", "secondary": "Initech",
                 "date": "2016 - 2018", "detail": ""},
            ],
        }
    ],
}


# ------------------------------------------------------------------ the gate
def test_clean_resume_asks_nothing():
    assert ats.bare_acronyms(CLEAN) == []


def test_clean_resume_never_calls_a_model(monkeypatch):
    def explode(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("the agent called a model with nothing to ask")

    monkeypatch.setattr(agents.llm, "is_configured", lambda: True)
    monkeypatch.setattr(agents.llm, "structured", explode)
    out = agents.review_ats(CLEAN)
    assert out.used_llm is False
    assert out.value == {"expansions": [], "note": ""}


def test_candidates_are_found_when_present():
    assert ats.bare_acronyms(MESSY) == ["PMP", "CKA"]


def test_real_technology_names_are_not_treated_as_acronyms():
    data = {**CLEAN, "skills": [{"label": "", "items": ["SQL", "HTML", "REST", "Go", "R"]}]}
    assert ats.bare_acronyms(data) == []


# --------------------------------------------------------------- the guard
def _review(**kw) -> AtsReview:
    return AtsReview(**kw)


def test_expansion_of_an_acronym_not_on_the_resume_is_dropped():
    out = agents._clean_ats_review(
        _review(expansions=[SkillExpansion(acronym="AWS", expanded="Amazon Web Services")]),
        ["PMP"],
    )
    assert out["expansions"] == []


def test_expansion_keeps_both_forms():
    out = agents._clean_ats_review(
        _review(expansions=[SkillExpansion(
            acronym="PMP", expanded="Project Management Professional")]),
        ["PMP"],
    )
    assert out["expansions"] == [
        {"acronym": "PMP", "expanded": "Project Management Professional (PMP)"}
    ]


def test_an_expansion_that_does_not_expand_is_dropped():
    out = agents._clean_ats_review(
        _review(expansions=[SkillExpansion(acronym="PMP", expanded="PMP")]), ["PMP"],
    )
    assert out["expansions"] == []


def test_a_double_wrapped_acronym_is_not_produced():
    out = agents._clean_ats_review(
        _review(expansions=[SkillExpansion(
            acronym="PMP", expanded="Project Management Professional (PMP)")]),
        ["PMP"],
    )
    assert out["expansions"][0]["expanded"].count("PMP") == 1


# ------------------------------------------------------------- degradation
def test_without_a_key_the_feature_goes_quiet_rather_than_guessing():
    """With no model there is no advice -- acronyms need judgement too.

    The sibling half of this feature (section relabelling) was deleted after
    it failed a pre-registered false-positive gate; see `_rule_ats_review`.
    What is left still refuses to guess: a wrong expansion is a fabricated
    credential on someone's resume.
    """
    out = agents.review_ats(MESSY)
    assert out.used_llm is False
    assert out.value == {"expansions": [], "note": ""}


def test_a_model_failure_falls_back_rather_than_raising(monkeypatch):
    monkeypatch.setattr(agents.llm, "is_configured", lambda: True)
    monkeypatch.setattr(
        agents.llm, "structured",
        lambda **k: (_ for _ in ()).throw(RuntimeError("provider down")),
    )
    out = agents.review_ats(MESSY)
    assert out.used_llm is False
    assert out.value["expansions"] == []


# ------------------------------------------------------------- prose salvage
def test_prose_reader_recovers_expansions_from_markdown():
    parsed = AtsReview.from_prose(
        "- PMP = Project Management Professional\n"
        "- CKA = Certified Kubernetes Administrator"
    )
    assert parsed is not None
    assert [e.acronym for e in parsed.expansions] == ["PMP", "CKA"]


def test_prose_reader_returns_none_on_junk():
    assert AtsReview.from_prose("I could not find anything.") is None
    assert AtsReview.from_prose("") is None


# ------------------------------------------------------------------- wiring
def test_the_score_is_identical_with_and_without_the_agent():
    """The agent is advisory. It must not be able to move the number."""
    assert (
        graphs.run_analysis(MESSY)["rule_result"]["overall_score"]
        == graphs.run_analysis(MESSY)["rule_result"]["overall_score"]
    )
    from app.services.heuristics import score_resume
    assert (
        graphs.run_analysis(MESSY)["rule_result"]["overall_score"]
        == score_resume(MESSY)["overall_score"]
    )


@pytest.mark.parametrize("data", [CLEAN, MESSY])
def test_graph_always_produces_a_review(data):
    state = graphs.run_analysis(data)
    assert "ats_review" in state
    assert set(state["ats_review"]) == {"expansions", "note"}
