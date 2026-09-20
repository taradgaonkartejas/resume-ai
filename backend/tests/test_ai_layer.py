"""AI-layer tests. None of these require an LLM key.

They pin the three properties that must hold whether or not a key is present:
the critic drops ungrounded drafts, the rule engine owns the number, and the
tailor graph's pause survives being rebuilt from the checkpoint store.
"""

import uuid

import pytest

from app.ai import agents, embeddings, graphs

RESUME = {
    "contact": {"name": "Test User", "email": "t@example.com", "phone": "+1 555"},
    "summary": {"text": "Platform engineer with six years of experience."},
    "experience": [
        {
            "company": "Acme",
            "role": "SRE",
            "bullets": [
                "Improved deploy reliability across the fleet.",
                "Cut build times by 40% with better caching.",
            ],
        }
    ],
    "projects": [],
    "education": [],
    "skills": [{"category": "Infra", "items": ["Kubernetes"]}],
}

JD = (
    "Hiring an SRE fluent in Prometheus, Grafana, Go and AWS. "
    "Observability and on-call ownership required."
)


# ------------------------------------------------------------------ embeddings
def test_local_embedding_is_deterministic_and_normalised():
    a = embeddings.local_embed("kubernetes platform engineering")
    b = embeddings.local_embed("kubernetes platform engineering")
    assert a == b
    assert len(a) == len(b)
    assert pytest.approx(sum(x * x for x in a), rel=1e-6) == 1.0


def test_cosine_ranks_related_text_higher():
    probe = embeddings.local_embed("prometheus grafana observability")
    near = embeddings.local_embed("grafana dashboards and prometheus alerts")
    far = embeddings.local_embed("baking sourdough bread at home")
    assert embeddings.cosine(probe, near) > embeddings.cosine(probe, far)


# ---------------------------------------------------------------------- critic
@pytest.mark.parametrize(
    "draft,reason",
    [
        (
            {
                "target_ref": "exp_0.bullet_0",
                "original_text": "Improved deploy reliability across the fleet.",
                "suggested_text": "Improved reliability by 93% for 40 teams.",
            },
            "invented metric",
        ),
        (
            {
                "target_ref": "exp_9.bullet_9",
                "original_text": "anything",
                "suggested_text": "anything else",
            },
            "unresolvable target_ref",
        ),
        (
            {
                "target_ref": "exp_0.bullet_0",
                "original_text": "Text that is not in the resume.",
                "suggested_text": "A rewrite.",
            },
            "tampered original_text",
        ),
        (
            {
                "target_ref": "exp_0.bullet_0",
                "original_text": "Improved deploy reliability across the fleet.",
                "suggested_text": "Led a team of eight to improve reliability.",
            },
            "unsupported claim",
        ),
    ],
)
def test_critic_rejects_ungrounded_drafts(draft, reason):
    verdict = agents.review_draft(draft, RESUME).value
    assert verdict.approved is False, reason
    assert verdict.severity == "fabrication"


def test_critic_approves_a_grounded_rewrite():
    draft = {
        "target_ref": "exp_0.bullet_0",
        "original_text": "Improved deploy reliability across the fleet.",
        "suggested_text": "Improved deploy reliability across the fleet using Go.",
    }
    assert agents.review_draft(draft, RESUME).value.approved is True


def test_critic_preserves_metrics_already_present():
    draft = {
        "target_ref": "exp_0.bullet_1",
        "original_text": "Cut build times by 40% with better caching.",
        "suggested_text": "Cut build times by 40% with better caching on AWS.",
    }
    assert agents.review_draft(draft, RESUME).value.approved is True


# -------------------------------------------------------------- analysis graph
def test_analysis_graph_is_deterministic():
    scores = {graphs.run_analysis(RESUME)["rule_result"]["overall_score"] for _ in range(5)}
    assert len(scores) == 1


def test_analysis_graph_visits_every_node():
    state = graphs.run_analysis(RESUME)
    assert [s["node"] for s in state["trace"]] == [
        "RetrieveATS",
        "RuleEngine",
        "ScoringAgent",
    ]
    assert state["commentary"]["headline"]


# ---------------------------------------------------------------- tailor graph
def test_tailor_graph_pauses_then_resumes_from_a_rebuilt_graph():
    thread = f"test-{uuid.uuid4()}"
    out = graphs.start_tailoring(
        thread, uuid.uuid4(), uuid.uuid4(), RESUME, JD
    )
    assert out["interrupted"] is True
    assert graphs.get_graph_state(thread)["next"] == ["AwaitUserReview"]

    # Drop the cached connection: the resume must come off disk, exactly as it
    # would in a second HTTP request or after a restart.
    graphs.reset_checkpointer()

    resumed = graphs.resume_tailoring(thread, "accepted")
    assert resumed["decision"] == "accepted"
    assert graphs.get_graph_state(thread)["finished"] is True


def test_tailor_suggestions_are_all_grounded():
    out = graphs.start_tailoring(
        f"test-{uuid.uuid4()}", uuid.uuid4(), uuid.uuid4(), RESUME, JD
    )
    assert out["suggestions"]
    for s in out["suggestions"]:
        verdict = agents.review_draft(s, RESUME).value
        assert verdict.approved is True, s


def test_tailor_graph_reports_gap_keywords():
    out = graphs.start_tailoring(
        f"test-{uuid.uuid4()}", uuid.uuid4(), uuid.uuid4(), RESUME, JD
    )
    gaps = out["match"]["gap_keywords"]
    assert "prometheus" in gaps and "grafana" in gaps
