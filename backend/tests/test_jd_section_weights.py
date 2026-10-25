"""Weighted JD-match (ATS) score, 2026-10-25 owner-specified rule.

Old behaviour: match_percent = matched_count / total_count -- a keyword
listed once in Skills counted the same as the same keyword proven inside a
job. That contradicted how ATS ranking actually reads sections. New rule,
exact weights pinned by the owner:

    Work Experience          45
    Skills                   30
    Projects                 15
    Summary/Education/extras 10
    -------------------------
                           100

Each JD keyword earns the weight of THE BEST section it appears in
(experience > skills > projects > other). Percent =
earned / (100 * n_keywords). A resume with no Projects section can never
break 85 match on that route alone -- the bucket is context, not a floor.
"""

from __future__ import annotations

from app.services.heuristics import match_keywords
from app.services.resume_ops import empty_resume


def _resume(*, exp: str = "", skills: str = "", proj: str = "", other: str = "") -> dict:
    data = empty_resume()
    if exp:
        data["experience"] = [{
            "company": "Acme", "role": "Engineer", "dates": "2022 - Present",
            "bullets": [exp],
        }]
    if skills:
        data["skills"] = [{"label": "Languages", "items": [skills]}]
    if proj:
        data["projects"] = [{"name": "Tool", "bullets": [proj]}]
    if other:
        data["summary"]["text"] = other
    return data


def test_keyword_in_experience_earns_45_points():
    out = match_keywords(_resume(exp="Ran Kubernetes clusters at scale."), ["kubernetes"])
    assert out["match_percent"] == 45.0


def test_keyword_in_skills_only_earns_30_points():
    out = match_keywords(_resume(skills="Kubernetes"), ["kubernetes"])
    assert out["match_percent"] == 30.0


def test_keyword_in_projects_only_earns_15_points():
    out = match_keywords(_resume(proj="Built a Kubernetes autoscaler demo."), ["kubernetes"])
    assert out["match_percent"] == 15.0


def test_keyword_in_summary_only_earns_10_points():
    out = match_keywords(_resume(other="Kubernetes operator at heart."), ["kubernetes"])
    assert out["match_percent"] == 10.0


def test_best_section_wins_when_keyword_is_everywhere():
    out = match_keywords(
        _resume(exp="Owned the Kubernetes platform.", skills="Kubernetes",
                proj="Kubernetes toys", other="Kubernetes everywhere."),
        ["kubernetes"],
    )
    assert out["match_percent"] == 45.0, "experience must beat skills/summary"


def test_weights_are_shared_across_keywords_not_sections():
    """A 4-keyword JD must not cap because 'only 2 fit' in one bucket."""
    data = _resume(
        exp="Led Kubernetes and Terraform rollouts.",
        skills="PostgreSQL", proj="Built with Rust.",
    )
    out = match_keywords(data, ["kubernetes", "terraform", "postgresql", "rust"])
    # 45+45+30+15 = 135 over 4*100 = 33.75 -> 33.8
    assert out["match_percent"] == 33.8
    assert out["matched_keywords"] == ["kubernetes", "terraform", "postgresql", "rust"]


def test_a_gap_keyword_still_earns_nothing():
    out = match_keywords(_resume(exp="Worked on APIs."), ["kubernetes", "python"])
    assert out["match_percent"] == 0.0
    assert out["gap_keywords"] == ["kubernetes", "python"]


def test_mixed_hit_rates_between_buckets():
    """Experience beats matched-anywhere: 2/2 matched but only one in a role."""
    out = match_keywords(
        _resume(exp="Ran Kubernetes.", skills="Terraform"),
        ["kubernetes", "terraform"],
    )
    assert out["matched_keywords"] == ["kubernetes", "terraform"]
    assert out["match_percent"] == (45.0 + 30.0) / 2


def test_no_keywords_still_scores_zero():
    assert match_keywords(_resume(exp="Anything."), [])["match_percent"] == 0.0
