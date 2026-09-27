"""Increment B — word-boundary matching and the scoring corrections.

Every case below was reproduced as a real failure before the fix
(AI-QUALITY-PLAN.md §1.1, §1.3-missed, §1.12, §2.5, §1.11).
"""

import pytest

from app.services.heuristics import (
    _METRIC_RE,
    ACTION_VERBS,
    BULLET_COACHED_MAX,
    BULLET_HARD_CAP,
    infer_role_tags,
    match_keywords,
)
from app.services.text_match import canonical_aliases, contains, count_hits, partition


def _resume(text: str) -> dict:
    return {
        "contact": {},
        "summary": {"text": text},
        "experience": [],
        "skills": [],
        "education": [],
        "projects": [],
    }


# ------------------------------------------------------------ the matcher
@pytest.mark.parametrize(
    "text,term,expected",
    [
        ("Organized a good team going forward.", "Go", False),   # going / good
        ("Strong algorithms and logos.", "Go", False),           # algorithm / logo
        ("I have read many reports.", "R", False),
        ("I write Go every day.", "Go", True),
        ("Languages: R, Python", "R", True),
        ("Built with C++ and Rust.", "C++", True),
        ("Built with ABC++ notation.", "C++", False),
        ("Shipped an ASP.NET service.", ".NET", True),
        ("machine  learning at scale", "machine learning", True),
        ("machine\nlearning at scale", "machine learning", True),
        ("We do machinelearning", "machine learning", False),
    ],
)
def test_contains_respects_word_boundaries(text, term, expected):
    assert contains(text, term) is expected


def test_contains_is_case_insensitive():
    assert contains("KUBERNETES in production", "kubernetes")


def test_partition_preserves_order_and_covers_every_term():
    terms = ["Go", "Python", "R", "Rust"]
    present, missing = partition("I write Python and Rust.", terms)
    assert present == ["Python", "Rust"]
    assert missing == ["Go", "R"]
    assert sorted(present + missing) == sorted(terms)


def test_count_hits_counts_each_term_once():
    assert count_hits("python python python java", ["python", "java", "go"]) == 2


def test_canonical_aliases_resolves_both_directions():
    assert "Kubernetes" in canonical_aliases("k8s")
    assert "k8s" in canonical_aliases("Kubernetes")


def test_canonical_aliases_passes_unknown_terms_through():
    assert canonical_aliases("Wombat") == ["Wombat"]


# --------------------------------------------------------- §1.1 match_keywords
def test_match_keywords_no_longer_false_positives():
    """Before: this exact resume reported match_percent 50.0."""
    result = match_keywords(
        _resume("Organized a good team going forward. Strong algorithms."),
        ["Go", "R", "Rust", "Kubernetes"],
    )
    assert result["matched_keywords"] == []
    assert result["match_percent"] == 0.0


def test_match_keywords_still_matches_real_mentions():
    result = match_keywords(
        _resume("I run Kubernetes clusters and write Go."), ["Go", "Kubernetes", "Rust"]
    )
    assert set(result["matched_keywords"]) == {"Go", "Kubernetes"}
    assert result["gap_keywords"] == ["Rust"]


def test_match_keywords_uses_taxonomy_aliases():
    """A JD asking for "k8s" is satisfied by a resume that writes "Kubernetes"."""
    result = match_keywords(_resume("I run Kubernetes and PostgreSQL."), ["k8s", "postgres"])
    assert set(result["matched_keywords"]) == {"k8s", "postgres"}


def test_match_percent_is_zero_for_no_keywords():
    assert match_keywords(_resume("anything"), [])["match_percent"] == 0.0


# ------------------------------------------------------------- §1.12 metrics
@pytest.mark.parametrize(
    "bullet",
    [
        "Reduced p99 latency by 40%.",
        "Saved $1.2M annually.",
        "Built modules adopted by 8 teams.",
        "Cut build time to 45 seconds.",
        "Grew throughput 3x.",
        "Served 40k requests per second.",
    ],
)
def test_real_metrics_are_credited(bullet):
    assert _METRIC_RE.search(bullet)


@pytest.mark.parametrize(
    "bullet",
    [
        "Shipped the billing rewrite in 2023.",
        "Joined the team in 1999.",
        "Refactored the parser for clarity.",
    ],
)
def test_bare_years_and_unquantified_prose_are_not_metrics(bullet):
    assert not _METRIC_RE.search(bullet)


# --------------------------------------------------- §2.5 + the missed bug
@pytest.mark.parametrize("bullet", ["Fulfilled customer orders.", "Downed tools today."])
def test_substring_verbs_no_longer_earn_credit(bullet):
    """"fulfilled" contains "led"; "downed" contains "owned"."""
    assert not any(contains(bullet, v) for v in ACTION_VERBS)


@pytest.mark.parametrize(
    "verb", ["spearheaded", "managed", "orchestrated", "streamlined", "negotiated"]
)
def test_common_strong_verbs_are_recognised(verb):
    assert verb in ACTION_VERBS


def test_action_verb_list_grew_substantially():
    assert len(ACTION_VERBS) > 60


# ------------------------------------------------------------ §1.11 thresholds
def test_coached_and_hard_cap_are_distinct_and_ordered():
    assert BULLET_COACHED_MAX == 30
    assert BULLET_HARD_CAP == 45
    assert BULLET_COACHED_MAX < BULLET_HARD_CAP


def test_overlong_finding_quotes_the_coached_number():
    long_bullet = " ".join(["word"] * (BULLET_HARD_CAP + 5))
    data = {
        "contact": {},
        "summary": {"text": ""},
        "experience": [{"company": "C", "role": "R", "dates": "2020", "bullets": [long_bullet]}],
        "skills": [],
        "education": [],
        "projects": [],
    }
    from app.services.heuristics import score_resume

    findings = score_resume(data)["category_scores"]["format"]["findings"]
    overlong = [f for f in findings if f["id"] == "format.overlong_bullets"]
    assert overlong, "a bullet past the hard cap should be flagged"
    assert str(BULLET_COACHED_MAX) in overlong[0]["fix_hint"]


# ------------------------------------------------------------- §2.6 role tags
def test_role_tags_use_boundaries_too():
    assert infer_role_tags(_resume("I run Kubernetes, Docker and Terraform on AWS")) == [
        "DevOps / SRE"
    ]


def test_role_tags_do_not_fire_on_substrings():
    """"api" must not match "capital", "ui" must not match "building"."""
    assert infer_role_tags(_resume("Capital allocation and building refurbishment.")) == []
