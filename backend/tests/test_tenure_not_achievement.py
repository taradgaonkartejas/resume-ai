"""`summary.no_metric` must not be satisfied by tenure alone.

Widening `_METRIC_RE` to accept the `N+` form was correct -- "2 years" and
"2+ years" are the same claim and scored differently -- but it had a
consequence nobody asked for: "5+ years of experience" became a quantified
achievement, and the finding stopped firing on every resume in the corpus at
once.

The fix is not to pick a side. Tenure is a real, load-bearing signal for an
ATS (Workday / Taleo / SuccessFactors / iCIMS parse years-of-experience and
screen on it) and is not an achievement for the human reading the shortlist.
These tests pin both halves:

  * tenure keeps matching `_METRIC_RE` everywhere else, so nothing that
    depended on the `N+` widening regresses;
  * the summary's achievement test runs on the text with tenure removed.

The expensive mistake this file guards against is over-stripping -- throwing
away a duration that IS the achievement ("cut onboarding by 2 years").
"""

import pytest

from app.services.heuristics import (
    _METRIC_RE,
    _strip_tenure,
    score_resume,
)


def _summary_finding(text: str):
    """Run the real scorer over a resume whose summary is `text`."""
    data = {
        "contact": {"name": "A B", "headline": "Engineer", "email": "a@b.com",
                    "phone": "+91 90000 00000", "location": "Pune, India",
                    "links": ["https://github.com/x"]},
        "summary": {"text": text},
        "experience": [], "projects": [], "education": [], "skills": [], "extras": [],
    }
    result = score_resume(data)
    for f in result["findings"]:
        if f["id"] == "summary.no_metric":
            return f
    return None


# --------------------------------------------------------------------------
# tenure alone does not satisfy the finding
# --------------------------------------------------------------------------

TENURE_ONLY = [
    "Software engineer with 4+ years of experience building Java services.",
    "Software engineer with 4 years of experience building Java services.",
    "5+ yrs in backend development.",
    "Over 7 years of experience in cloud infrastructure.",
    "Backend developer. 3 years. Java, Spring, Postgres.",
    "Engineer with approximately 6 years of experience in distributed systems.",
    "Full-stack engineer with 2+ years building production microservices.",
]


@pytest.mark.parametrize("text", TENURE_ONLY)
def test_tenure_alone_still_raises_no_metric(text):
    f = _summary_finding(text)
    assert f is not None, f"tenure was accepted as an achievement: {text!r}"


@pytest.mark.parametrize("text", TENURE_ONLY)
def test_tenure_only_is_labelled_as_such(text):
    """The card must not tell someone who typed a number that there is none."""
    f = _summary_finding(text)
    assert f["meta"]["tenure_only"] is True
    assert "years of experience" in f["message"]


def test_tenure_only_advice_says_keep_the_years():
    """ATS screens on tenure. The advice must be 'add', never 'replace'."""
    f = _summary_finding("Engineer with 5+ years of experience in Python.")
    hint = f["fix_hint"].lower()
    assert "keep" in hint and "ats" in hint
    for bad in ("remove", "delete", "drop", "instead of"):
        assert bad not in hint, f"advice tells the user to discard tenure: {f['fix_hint']!r}"


def test_no_number_at_all_keeps_the_original_wording():
    """Two different problems must not share one message."""
    f = _summary_finding("Backend engineer passionate about clean, tested code.")
    assert f is not None
    assert f["meta"]["tenure_only"] is False
    assert f["message"] == "Summary has no quantified achievement"


# --------------------------------------------------------------------------
# a real achievement still clears it, including alongside tenure
# --------------------------------------------------------------------------

ACHIEVEMENT = [
    "Engineer with 4+ years of experience; cut p95 latency by 40%.",
    "3 years of experience leading a team of 8 engineers.",
    "Served 2M users across 3 regions.",
    "10+ years. Scaled the platform to 1.2M requests/day.",
    "Full Stack Engineer with 2+ years of experience, accelerating processing by 400%.",
    "Saved $1.2M in annual cloud spend.",
]


@pytest.mark.parametrize("text", ACHIEVEMENT)
def test_a_real_metric_clears_the_finding(text):
    assert _summary_finding(text) is None, f"real achievement was missed: {text!r}"


def test_tenure_plus_achievement_clears_it():
    """Tenure must not mask a metric that is genuinely there."""
    assert _summary_finding(
        "Engineer with 6+ years of experience who reduced build times by 70%."
    ) is None


# --------------------------------------------------------------------------
# over-stripping: a duration that IS the achievement must survive
# --------------------------------------------------------------------------

DURATION_AS_RESULT = [
    "Reduced onboarding from 3 years to 6 months.",
    "Cut contract cycle time by 2 years.",
    "Saved 2 years of manual effort across the org.",
    "Delivered the migration 2 years ahead of schedule.",
    "Shipped the platform rewrite 2 years early.",
]


@pytest.mark.parametrize("text", DURATION_AS_RESULT)
def test_a_duration_that_is_the_result_is_not_stripped(text):
    assert _METRIC_RE.search(_strip_tenure(text)), (
        f"stripped an achievement, not a tenure claim: {text!r} -> {_strip_tenure(text)!r}"
    )
    assert _summary_finding(text) is None


# --------------------------------------------------------------------------
# the N+ widening is NOT reverted -- tenure still counts as a metric
# everywhere other than the summary achievement test
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "2+ years", "10+ engineers", "20+%", "10k+ requests", "2 years",
])
def test_plus_form_still_matches_the_general_metric_regex(text):
    assert _METRIC_RE.search(text), (
        "the N+ widening was reverted; this fix was supposed to be narrower"
    )


def test_experience_bullets_are_not_affected():
    """Scope: this change touches the SUMMARY achievement test only.

    A bullet reading "2+ years leading the migration" is still quantified.
    Pinning it here so widening the scope later is a deliberate, visible
    decision rather than a side effect.
    """
    data = {
        "contact": {"name": "A B", "headline": "", "email": "a@b.com",
                    "phone": "+91 90000 00000", "location": "Pune", "links": []},
        "summary": {"text": "Engineer who cut latency by 40%."},
        "experience": [{
            "company": "Acme Corp", "role": "Engineer", "dates": "2020 - 2024",
            "start_date": "2020", "end_date": "2024", "current": False,
            "bullets": ["Spent 2+ years leading the payments migration"],
        }],
        "projects": [], "education": [], "skills": [], "extras": [],
    }
    ids = {f["id"] for f in score_resume(data)["findings"]}
    assert "experience.unquantified_bullets" not in ids


# --------------------------------------------------------------------------
# calendar years are still not metrics (the original exclusion)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Engineer since 2019 working on backend systems.",
    "Graduated in 2024 with a focus on distributed systems.",
])
def test_a_calendar_year_is_still_not_a_metric(text):
    assert _summary_finding(text) is not None
