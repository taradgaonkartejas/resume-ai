""""What's Working Well" must not become a flattery machine.

Every other part of the analysis is adversarial towards the resume: the rule
engine emits findings, and a finding is by definition a problem. This is the
one feature asked to say something nice, which makes it the one feature with
an incentive to invent something nice.

The prompt asks for evidence. These tests cover what the prompt cannot
guarantee -- `agents._clean_strengths`, which is deterministic and therefore
actually provable:

  1. a category scoring under half marks gets no praise at all
  2. a claim containing a number absent from the resume is dropped
  3. the list is capped, because padding to a fixed length is the failure
     mode this feature invites
"""

from __future__ import annotations

from app.ai import schemas
from app.ai.agents import (
    MAX_STRENGTHS_PER_CATEGORY,
    STRENGTH_SCORE_FLOOR,
    _clean_strengths,
)

RESUME = (
    "Sam Rivera\n"
    "Platform engineer with six years of cloud infrastructure work.\n"
    "Cut build times from 20 minutes to 6 minutes.\n"
    "Led a team of 4 engineers.\n"
)


def _rules(**scores) -> dict:
    """category_scores with a max of 20 each unless told otherwise."""
    return {
        "category_scores": {
            name: {"score": score, "max": 20, "notes": []}
            for name, score in scores.items()
        }
    }


def _strengths(**kwargs) -> schemas.CategoryStrengths:
    return schemas.CategoryStrengths(**kwargs)


def test_a_failing_category_gets_no_praise():
    """The number is printed next to the prose. They must not disagree."""
    out = _clean_strengths(
        _strengths(summary=["Crisp, well-targeted summary."]),
        _rules(summary=4),  # 4/20 = 20%, far below the floor
        RESUME,
    )
    assert out["summary"] == []


def test_a_strong_category_keeps_its_praise():
    out = _clean_strengths(
        _strengths(summary=["Opens with six years of cloud infrastructure."]),
        _rules(summary=18),
        RESUME,
    )
    assert out["summary"] == ["Opens with six years of cloud infrastructure."]


def test_the_floor_is_applied_at_the_boundary():
    """Exactly on the floor is allowed; a hair under is not."""
    on_floor = int(20 * STRENGTH_SCORE_FLOOR)
    assert _clean_strengths(_strengths(format=["Clean layout."]),
                            _rules(format=on_floor), RESUME)["format"]
    assert not _clean_strengths(_strengths(format=["Clean layout."]),
                                _rules(format=on_floor - 1), RESUME)["format"]


def test_a_number_absent_from_the_resume_is_dropped():
    """The same digits-only guard that stops a writer inventing a metric.

    A strength claiming a number nobody can find is a fabrication wearing a
    friendlier voice.
    """
    out = _clean_strengths(
        _strengths(experience=[
            "Quantified the build-time cut from 20 minutes to 6 minutes.",
            "Demonstrates 40% cost savings across the platform.",
        ]),
        _rules(experience=18),
        RESUME,
    )
    assert out["experience"] == [
        "Quantified the build-time cut from 20 minutes to 6 minutes."
    ]


def test_numbers_present_in_the_resume_survive():
    out = _clean_strengths(
        _strengths(experience=["Led a team of 4 engineers."]),
        _rules(experience=18),
        RESUME,
    )
    assert out["experience"] == ["Led a team of 4 engineers."]


def test_the_list_is_capped():
    # Digit-free on purpose. The first draft of this test used
    # f"Strength {i}" and only "Strength 4" and "Strength 6" survived --
    # because 4 and 6 appear in RESUME ("team of 4", "6 minutes") and the
    # other eight digits do not. The number guard was doing its job; the
    # fixture was wrong. Worth keeping as a note: incidental digits in a
    # strength are treated as claims, which is the conservative direction.
    out = _clean_strengths(
        _strengths(format=[f"Strength {chr(97 + i)}" for i in range(10)]),
        _rules(format=20),
        RESUME,
    )
    assert len(out["format"]) == MAX_STRENGTHS_PER_CATEGORY


def test_the_cap_is_applied_after_the_number_filter():
    """Order matters: filter, then cap.

    Capping first would let three fabrications crowd out three real
    strengths and ship a list that is entirely invented.
    """
    out = _clean_strengths(
        _strengths(experience=[
            "Claims 91% uptime.",      # dropped: no 91 in the resume
            "Claims 72 deployments.",  # dropped: no 72 either
            "Led a team of four.",
            "Cut build times to six minutes.",
            "Uses strong action verbs throughout.",
            "Mentions cloud infrastructure.",
        ]),
        _rules(experience=18),
        RESUME,
    )
    assert out["experience"] == [
        "Led a team of four.",
        "Cut build times to six minutes.",
        "Uses strong action verbs throughout.",
    ]


def test_blank_entries_are_dropped():
    out = _clean_strengths(
        _strengths(contact=["", "   ", "Phone and email both present."]),
        _rules(contact=20),
        RESUME,
    )
    assert out["contact"] == ["Phone and email both present."]


def test_an_unknown_category_is_not_invented():
    """Only categories the rule engine actually scores may appear."""
    out = _clean_strengths(
        _strengths(summary=["Good."]), {"category_scores": {}}, RESUME
    )
    assert out["summary"] == []


def test_empty_in_empty_out():
    out = _clean_strengths(_strengths(), _rules(summary=20, contact=20), RESUME)
    assert all(v == [] for v in out.values())


def test_a_zero_max_category_cannot_divide_by_zero():
    out = _clean_strengths(
        _strengths(format=["Nice."]),
        {"category_scores": {"format": {"score": 0, "max": 0}}},
        RESUME,
    )
    assert out["format"] == []


# ---------------------------------------------------------------- wiring ---
def test_strengths_ride_on_the_existing_commentary_schema():
    """One call, not two: the notes and the praise must agree, and at 40
    analyses a month a second scoring call is not free."""
    fields = schemas.ScoringCommentary.model_fields
    assert "strengths" in fields
    assert fields["strengths"].annotation is schemas.CategoryStrengths


def test_the_strengths_schema_is_groq_strict_safe():
    """Nested models are the easy place to reintroduce an open map.

    `CategoryStrengths` is never handed to `llm.structured()` directly, so
    it is not in LLM_SCHEMAS; it reaches the wire inside ScoringCommentary's
    `$defs`, which is exactly where a dict[str, list[str]] would hide.
    """
    schema = schemas.ScoringCommentary.model_json_schema()
    assert "CategoryStrengths" in schema.get("$defs", {})

    def open_maps(node, path="$"):
        bad = []
        if isinstance(node, dict):
            if node.get("type") == "object" and isinstance(
                node.get("additionalProperties"), dict
            ):
                bad.append(path)
            for key, value in node.items():
                bad += open_maps(value, f"{path}/{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                bad += open_maps(value, f"{path}[{i}]")
        return bad

    assert open_maps(schema) == []


def test_the_rule_engine_fallback_claims_no_strengths(monkeypatch):
    """With no model there is nothing to praise with.

    Every rule-engine check emits a finding or stays silent, so "this check
    passed" is the only raw material available -- and turning that into
    praise is precisely the padding the LLM path is filtered to prevent.
    """
    from app.ai import agents

    monkeypatch.setattr(agents.llm, "is_configured", lambda: False)
    outcome = agents.score_commentary(
        {"contact": {"name": "Sam"}},
        {
            "overall_score": 72,
            "category_scores": {
                "contact": {"score": 15, "max": 15, "notes": []},
                "summary": {"score": 20, "max": 20, "notes": []},
                "experience": {"score": 45, "max": 45, "notes": []},
                "format": {"score": 20, "max": 20, "notes": []},
            },
        },
    )
    assert outcome.used_llm is False
    assert outcome.value["strengths"] == {}


# --------------------------------------------------------------------- live
# The filter above is a guarantee; this is the MODEL's behaviour, which only a
# real call can show. Run with `--live` (or `make test-live`) and keys set.
import pytest  # noqa: E402

WEAK_RESUME = {
    "contact": {"name": "A B", "email": "", "phone": "", "location": "", "links": []},
    "summary": {"text": "Hard worker. Proven track record. Team player."},
    "experience": [
        {"company": "", "role": "Worker", "dates": "",
         "bullets": ["Responsible for various tasks.", "Helped with things."]},
    ],
    "projects": [], "education": [], "skills": [],
}


@pytest.mark.live
def test_live_a_weak_resume_is_not_flattered():
    """The honest answer here is silence, in most or all categories.

    This is the vacuity failure from `writer_report` wearing a friendlier
    hat: a model that always returns three strengths passes a
    "has strengths" check trivially while telling the user nothing. The
    resume above has no contact details, a summary made entirely of filler,
    and two unquantified duty-statements.

    The assertion is deliberately loose -- it grades "did not pad", not
    exact wording -- because one unlucky judgement should not turn the
    build red. `_clean_strengths` is where the hard guarantees live.
    """
    from app.ai import agents
    from app.services.heuristics import score_resume

    rule_result = score_resume(WEAK_RESUME)
    outcome = agents.score_commentary(WEAK_RESUME, rule_result)

    strengths = outcome.value.get("strengths") or {}
    total = sum(len(v) for v in strengths.values())
    assert total <= 2, f"flattered a weak resume with {total} strengths: {strengths}"

    # And nothing may be claimed for a category the engine is failing.
    for name, items in strengths.items():
        cat = rule_result["category_scores"][name]
        if cat["score"] < cat["max"] * agents.STRENGTH_SCORE_FLOOR:
            assert items == [], f"{name} is failing but claims {items}"
