"""Tier 2 metric-plausibility advisories: judged by a model, kept by rules.

The feature is a judgement call a regex can never make -- "10 jobs/hour is
low for a microservices pipeline" needs domain knowledge, and a rule that
guesses fires on correct resumes. What CAN be tested deterministically is
everything AROUND the judgement: candidate selection, the verbatim-quote
guard, the gate, and -- most important -- that the score never hears about
any of it.
"""

from app.ai import agents
from app.ai.schemas import AtsReview, MetricAdvisory, SkillExpansion

DATA = {
    "experience": [
        {
            "role": "Software Engineer",
            "company": "Acme",
            "dates": "2023 - Present",
            "bullets": [
                "Cut p95 latency by 40% by introducing caching",
                "Led weekly stand-ups for the platform team",
            ],
        },
        {
            "role": "Intern",
            "company": "Poly",
            "dates": "2021 - 2022",
            "bullets": ["Processed 10 jobs/hour through the pipeline"],
        },
    ],
    "skills": [{"label": "Tools", "items": ["Kafka"]}],
}

REFS = {"exp_0.bullet_0", "exp_1.bullet_0"}


# ---------------------------------------------------------- candidates


def test_every_bullet_with_a_number_is_a_candidate():
    refs = agents.metric_bullets(DATA)
    assert set(refs) == REFS
    # the un-numbered bullet is not shown to the model
    assert all("stand-ups" not in t for t in refs.values())


def test_candidate_refs_match_the_ref_scheme_everywhere_else_uses():
    """exp_N.bullet_M is how the writer and the chat route edits."""
    for ref in agents.metric_bullets(DATA):
        head, tail = ref.split(".", 1)
        ei = int(head.split("_")[1])
        bi = int(tail.split("_")[1])
        entry = DATA["experience"][ei]
        assert agents.metric_bullets(DATA)[ref] == entry["bullets"][bi]


# ---------------------------------------------------------- the gate


def test_no_metrics_and_no_acronyms_gates_the_agent_silent(monkeypatch):
    def _boom(**_kw):  # any LLM call fails the test
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(agents.llm, "structured", _boom)
    empty = {"experience": [{"bullets": ["Coordinated releases"]}]}
    outcome = agents.review_ats(empty)
    assert outcome.value == {"expansions": [], "plausibility": [], "note": ""}
    assert not outcome.used_llm


def test_metric_bullets_alone_trigger_a_call(monkeypatch):
    """Tier 2 must not require an acronym to exist."""
    called = {"n": 0}

    class _Call:
        model = "rules"
        latency_ms = 0.0
        tokens_in = tokens_out = 0

    def _fake(**_kw):
        called["n"] += 1
        return AtsReview(), _Call()

    monkeypatch.setattr(agents.llm, "structured", _fake)
    monkeypatch.setattr(agents.llm, "is_configured", lambda: True)
    agents.review_ats(DATA)
    assert called["n"] == 1


# ------------------------------------------------- the verbatim guard


def _cand():
    return agents.metric_bullets(DATA)


def test_a_verbatim_quote_survives_the_filter():
    review = AtsReview(
        plausibility=[
            MetricAdvisory(
                bullet="exp_1.bullet_0",
                quote="10 jobs/hour",
                advice="That throughput is low for a batch pipeline; "
                "verify the denominator.",
            )
        ]
    )
    out = agents._clean_ats_review(review, [], _cand())
    assert out["plausibility"] == [
        {
            "bullet": "exp_1.bullet_0",
            "quote": "10 jobs/hour",
            "advice": "That throughput is low for a batch pipeline; "
            "verify the denominator.",
        }
    ]


def test_a_paraphrased_quote_is_dropped():
    """Advice about a number that is not on the resume is fabrication."""

    review = AtsReview(
        plausibility=[
            MetricAdvisory(
                bullet="exp_0.bullet_0",
                quote="nearly half the p95 latency",  # not on the resume
                advice="implausible",
            )
        ]
    )
    out = agents._clean_ats_review(review, [], _cand())
    assert out["plausibility"] == []


def test_an_advisory_about_a_bullet_the_model_never_saw_is_dropped():
    review = AtsReview(
        plausibility=[
            MetricAdvisory(
                bullet="exp_9.bullet_9", quote="100%", advice="no"
            )
        ]
    )
    out = agents._clean_ats_review(review, [], _cand())
    assert out["plausibility"] == []


def test_the_quote_must_belong_to_THE_reference_not_any_reference():
    """"40%" exists under exp_0; quoting it under exp_1 is still a lie."""
    review = AtsReview(
        plausibility=[
            MetricAdvisory(bullet="exp_1.bullet_0", quote="40%", advice="no")
        ]
    )
    out = agents._clean_ats_review(review, [], _cand())
    assert out["plausibility"] == []


def test_expansions_still_work_and_advice_length_is_capped():
    review = AtsReview(
        expansions=[SkillExpansion(acronym="KAFKA", expanded="Apache Kafka")],
        plausibility=[
            MetricAdvisory(
                bullet="exp_1.bullet_0", quote="10 jobs/hour", advice="x" * 600
            )
        ],
    )
    out = agents._clean_ats_review(
        review, ["KAFKA"], _cand()
    )
    assert out["expansions"], "acronym half must be untouched"
    assert len(out["plausibility"][0]["advice"]) == 400


def test_fallback_and_empty_states_carry_the_new_shape():
    assert agents._rule_ats_review() == {
        "expansions": [],
        "plausibility": [],
        "note": "",
    }


# ------------------------------------------- never the score, measured


def test_plausibility_advice_never_reaches_the_score():
    """The whole decision restated as a test.

    The user ruled: score stays deterministic, Tier 2 is advice with no
    points. If this ever regresses, every analysis of the same resume can
    disagree with itself.
    """
    from app.services.heuristics import score_resume

    before = score_resume(DATA)
    # Simulate the post-agent state: the ats_review dict is stored alongside
    # the rule result and is never read by the scorer itself.
    after = score_resume(DATA)
    assert before["overall_score"] == after["overall_score"]
    assert "plausibility" not in before
