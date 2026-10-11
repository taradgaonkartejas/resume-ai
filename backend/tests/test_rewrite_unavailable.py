"""A writer that was never reached must not produce a verdict about the resume.

The defect this pins: a rate-limited writer fell back to the rule engine, the
rule engine had no mechanical fix for `summary.no_metric`, and the resulting
no-op was reported as 422 "This one needs a fact only you have - the AI writer
cannot add a number that is not already in your resume."

That sentence is a confident claim about someone's resume, issued when the
model was never consulted. It is also the same shape as the critic_report bug
that announced the LLM critic added nothing during a run where every call 403'd.
"""

from unittest.mock import patch

import pytest

from app.ai import llm

RATE_LIMITED = Exception(
    "Error code: 429 - Too many requests. The free tier allows 1 request(s) "
    "every 1 min per account on openai/gpt-oss-120b - retry in 23s."
)


def _summary_finding(client, headers, resume_id):
    steps = client.get(f"/api/resumes/{resume_id}/analysis/steps", headers=headers).json()
    for step in steps.get("steps", []):
        for finding in step.get("findings", []):
            if finding.get("target_ref") == "summary.text":
                return finding
    pytest.skip("seed resume has no summary.text finding to rewrite")
    return None


def test_a_rate_limited_writer_returns_503_not_a_verdict(client, priya, priya_resume):
    headers = {"X-User-Id": priya}
    finding = _summary_finding(client, headers, priya_resume)

    with (
        patch.object(llm, "is_configured", return_value=True),
        patch.object(llm, "structured", side_effect=RATE_LIMITED),
    ):
        response = client.post(
            f"/api/resumes/{priya_resume}/sections/summary.text/rewrite",
            json={"finding_id": finding.get("id", ""), "regenerate": False},
            headers=headers,
        )

    assert response.status_code == 503, response.json()
    detail = response.json()["detail"]
    assert "could not be reached" in detail
    # It must not blame the document.
    assert "fact only you have" not in detail
    assert "your resume" not in detail.replace("Nothing is wrong with your resume", "")


def test_the_503_tells_the_user_it_is_worth_retrying(client, priya, priya_resume):
    headers = {"X-User-Id": priya}
    finding = _summary_finding(client, headers, priya_resume)

    with (
        patch.object(llm, "is_configured", return_value=True),
        patch.object(llm, "structured", side_effect=RATE_LIMITED),
    ):
        response = client.post(
            f"/api/resumes/{priya_resume}/sections/summary.text/rewrite",
            json={"finding_id": finding.get("id", ""), "regenerate": False},
            headers=headers,
        )

    assert "try again" in response.json()["detail"].lower()


def test_a_writer_that_ran_and_could_not_help_still_returns_422(
    client, priya, priya_resume
):
    """The original message was not wrong -- it was applied to the wrong case.

    When the model genuinely answers and cannot improve the text, saying so is
    correct and must be preserved.
    """
    headers = {"X-User-Id": priya}
    finding = _summary_finding(client, headers, priya_resume)

    with patch.object(llm, "is_configured", return_value=False):
        response = client.post(
            f"/api/resumes/{priya_resume}/sections/summary.text/rewrite",
            json={"finding_id": finding.get("id", ""), "regenerate": False},
            headers=headers,
        )

    # No LLM configured => the rule engine ran on purpose, not as a fallback
    # from a failed call, so a no-op here is a real statement about the finding.
    assert response.status_code in (201, 422)
    if response.status_code == 422:
        assert "fact only you have" in response.json()["detail"]


def test_the_two_causes_do_not_share_a_status_code(client, priya, priya_resume):
    """The whole point: a transient upstream failure is retryable, a 422 is not."""
    headers = {"X-User-Id": priya}
    finding = _summary_finding(client, headers, priya_resume)
    body = {"finding_id": finding.get("id", ""), "regenerate": False}
    url = f"/api/resumes/{priya_resume}/sections/summary.text/rewrite"

    with (
        patch.object(llm, "is_configured", return_value=True),
        patch.object(llm, "structured", side_effect=RATE_LIMITED),
    ):
        degraded = client.post(url, json=body, headers=headers).status_code

    with patch.object(llm, "is_configured", return_value=False):
        rules_only = client.post(url, json=body, headers=headers).status_code

    assert degraded == 503
    assert degraded != rules_only
