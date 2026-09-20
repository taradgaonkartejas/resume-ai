"""Acceptance gate 3 — the score is reproducible.

Scoring is a rule engine, not an LLM, so identical input must yield an
identical number every time.
"""

from app.services.heuristics import extract_keywords, match_keywords, score_resume

RESUME = {
    "contact": {
        "name": "Priya Sharma",
        "email": "priya@example.com",
        "phone": "+91 98765 43210",
        "location": "Pune",
        "links": ["github.com/priya"],
    },
    "summary": {
        "text": (
            "Site reliability engineer with six years of production experience "
            "who reduced deployment time by 65% across 40 services."
        )
    },
    "experience": [
        {
            "company": "Acme",
            "role": "Senior SRE",
            "dates": "2021 - Present",
            "bullets": [
                "Led migration of 40 services to Kubernetes, cutting deploy time 65%",
                "Reduced pager volume by 45% by rewriting alert rules",
            ],
        }
    ],
    "projects": [],
    "education": [{"school": "COEP", "degree": "B.Tech", "dates": "2015 - 2019"}],
    "skills": [{"label": "Platform", "items": ["Kubernetes", "Terraform"]}],
}


def test_score_is_stable_across_runs():
    runs = [score_resume(RESUME) for _ in range(10)]
    first = runs[0]
    assert all(r == first for r in runs)


def test_weights_sum_to_one_hundred():
    from app.services.heuristics import WEIGHTS

    assert sum(WEIGHTS.values()) == 100


def test_category_scores_never_exceed_max():
    result = score_resume(RESUME)
    for name, cat in result["category_scores"].items():
        assert 0 <= cat["score"] <= cat["max"], name
    assert result["overall_score"] == sum(
        c["score"] for c in result["category_scores"].values()
    )


def test_empty_resume_scores_zero_ish():
    empty = {
        "contact": {},
        "summary": {"text": ""},
        "experience": [],
        "projects": [],
        "education": [],
        "skills": [],
    }
    result = score_resume(empty)
    assert result["overall_score"] < 15


def test_keyword_extraction_is_deterministic():
    jd = "We need Kubernetes, Terraform and AWS experience. Kubernetes is essential."
    runs = [extract_keywords(jd) for _ in range(5)]
    assert all(r == runs[0] for r in runs)
    assert "kubernetes" in runs[0]


def test_match_percent_is_deterministic():
    keywords = ["kubernetes", "terraform", "rust", "haskell"]
    runs = [match_keywords(RESUME, keywords) for _ in range(5)]
    assert all(r == runs[0] for r in runs)
    assert runs[0]["match_percent"] == 50.0


def test_analysis_endpoint_is_deterministic(client, priya, priya_resume):
    headers = {"X-User-Id": priya}
    first = client.post(f"/api/resumes/{priya_resume}/analyze", headers=headers).json()
    second = client.post(f"/api/resumes/{priya_resume}/analyze", headers=headers).json()
    assert first["overall_score"] == second["overall_score"]
    assert first["category_scores"] == second["category_scores"]
    assert first["role_tags"] == second["role_tags"]
