"""A degraded critic must be visible, not just logged.

When the LLM critic errors, review_draft falls back to _rule_critique. That
fallback is blind to semantic fabrication (see tests/critic_cases.py), so a
silent swap replaces the anti-fabrication gate with something materially
weaker. It has to show up in the agent trace and in agent_runs.
"""

from unittest.mock import patch

from app.ai import agents, llm

DATA = {
    "experience": [
        {
            "company": "Acme",
            "role": "Eng",
            "dates": "",
            "bullets": ["Built Terraform modules adopted by 8 teams."],
        }
    ]
}
DRAFT = {
    "target_ref": "exp_0.bullet_0",
    "original_text": "Built Terraform modules adopted by 8 teams.",
    "suggested_text": "Drove Terraform adoption across 8 teams.",
    "keywords": [],
}


def test_no_api_key_is_not_a_degradation():
    """Running rules-only without a key is the documented offline mode."""
    outcome = agents.review_draft(DRAFT, DATA)
    assert outcome.status == "ok"
    assert outcome.used_llm is False


def test_an_llm_failure_is_marked_degraded():
    with (
        patch.object(llm, "is_configured", return_value=True),
        patch.object(llm, "structured", side_effect=RuntimeError("json_schema unsupported")),
    ):
        outcome = agents.review_draft(DRAFT, DATA)
    assert outcome.status == "degraded"


def test_the_degradation_names_the_cause():
    with (
        patch.object(llm, "is_configured", return_value=True),
        patch.object(llm, "structured", side_effect=RuntimeError("json_schema unsupported")),
    ):
        outcome = agents.review_draft(DRAFT, DATA)
    assert "json_schema unsupported" in " ".join(outcome.notes)


def test_a_degraded_critic_still_returns_a_usable_verdict():
    """Degrading must not break the request, only flag it."""
    with (
        patch.object(llm, "is_configured", return_value=True),
        patch.object(llm, "structured", side_effect=RuntimeError("boom")),
    ):
        outcome = agents.review_draft(DRAFT, DATA)
    assert outcome.value.approved in (True, False)


def test_hard_checks_run_before_any_llm_and_are_never_degraded():
    """An unresolvable ref is rejected without consulting a model at all."""
    with patch.object(llm, "is_configured", side_effect=AssertionError("must not be called")):
        outcome = agents.review_draft({**DRAFT, "target_ref": "exp_9.bullet_9"}, DATA)
    assert outcome.value.approved is False
    assert outcome.value.severity == "fabrication"


# --------------------------------------------------------------- trace plumbing
def test_tailoring_record_forwards_status(monkeypatch):
    """_record used to drop status, so 'degraded' never reached agent_runs."""
    from app.services.tailoring_service import TailoringService

    seen = []

    class FakeRuns:
        def record(self, **kwargs):
            seen.append(kwargs)

    svc = object.__new__(TailoringService)
    svc.runs = FakeRuns()
    svc._record(
        [{"agent": "critic", "status": "degraded", "model": "rule-engine"}],
        user_id=None,
        thread_id="t",
    )
    assert seen and seen[0]["status"] == "degraded"


def test_ok_status_is_the_default(monkeypatch):
    from app.services.tailoring_service import TailoringService

    seen = []

    class FakeRuns:
        def record(self, **kwargs):
            seen.append(kwargs)

    svc = object.__new__(TailoringService)
    svc.runs = FakeRuns()
    svc._record([{"agent": "writer", "model": "m"}], user_id=None, thread_id="t")
    assert seen[0]["status"] == "ok"


def test_a_degraded_critic_reaches_the_admin_agent_runs_feed(client, priya):
    """End to end: break the critic LLM, run a tailor, see it in /admin.

    This is the assertion that matters -- everything above proves the flag is
    set, this proves an operator can actually see it.
    """
    headers = {"X-User-Id": priya}
    made = client.post("/api/resumes", json={"title": "Degraded probe"}, headers=headers).json()
    client.put(
        f"/api/resumes/{made['id']}/data",
        json={
            "structured_data": {
                "contact": {"name": "Jane Doe", "email": "j@x.com"},
                "summary": {"text": "Platform engineer with eight years of experience."},
                "experience": [
                    {
                        "company": "Acme",
                        "role": "Eng",
                        "dates": "2020-2024",
                        "bullets": ["Built Terraform modules adopted by 8 teams."],
                    }
                ],
                "skills": [],
                "education": [],
                "projects": [],
            }
        },
        headers=headers,
    )

    with (
        patch.object(llm, "is_configured", return_value=True),
        patch.object(llm, "structured", side_effect=RuntimeError("json_schema unsupported")),
        patch.object(llm, "complete", side_effect=RuntimeError("json_schema unsupported")),
    ):
        client.post(
            f"/api/resumes/{made['id']}/tailor",
            json={"jd_title": "SRE", "jd_content": "Kubernetes Terraform AWS", "fork": True},
            headers=headers,
        )

    runs = client.get("/api/admin/agent-runs", headers=headers).json()
    critic = [r for r in runs if r.get("agent") == "critic"]
    assert critic, "the critic should appear in agent_runs at all"
    assert any(r.get("status") == "degraded" for r in critic), (
        f"no degraded critic run recorded: {[(r.get('agent'), r.get('status')) for r in runs]}"
    )
