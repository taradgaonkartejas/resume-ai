"""The scoring sub-budget guard Part 3 called for, and it did not exist.

`WEIGHTS` sums to 100 and `Δscore == points` is asserted in three places, so
a new scored finding cannot add to the total -- it has to take its points
from inside its own category. Part 3 Tier 1 did exactly that, re-slicing
`summary` and `experience` to free 3 and 12 points.

Nothing checked the arithmetic. If a future check takes 3 points and the
category's other checks are not reduced by 3, the category quietly scores
out of 23 while advertising 20, every card's "+N points" becomes a lie, and
the first symptom is a user complaining that fixing three "+5" items gained
them seven.

Two guards, because a declaration can agree with itself and still be wrong:

1. the declared `SUB_BUDGETS` sum to `WEIGHTS`;
2. a resume with no defects actually scores the full weight in every
   category -- which is what proves the declaration matches the code.
"""

import copy

import pytest

from app.services.heuristics import SUB_BUDGETS, WEIGHTS, score_resume


def _clean() -> dict:
    """A resume with nothing wrong with it. Must score 100."""
    return copy.deepcopy({
        "contact": {
            "name": "Priya Sharma", "headline": "Site Reliability Engineer",
            "email": "priya@example.com", "phone": "+91 90000 00000",
            "location": "Pune, IN", "links": ["github.com/priya"],
        },
        "summary": {"text": (
            "Site reliability engineer with six years running production "
            "infrastructure for high-traffic services across three regions. "
            "Led a platform migration that reduced deploy time by 65 percent "
            "and cut pager volume by 45 percent for 80 engineers."
        )},
        "experience": [
            {
                "company": "Acme", "role": "Senior SRE",
                "start_date": "Mar 2022", "current": True,
                "bullets": [
                    "Led migration of 40 services to Kubernetes, cutting "
                    "deploy time by 65%",
                    "Reduced pager volume by 45% by rewriting alert rules "
                    "around service level objectives",
                    "Built Terraform modules adopted by 8 engineering teams",
                    "Automated certificate rotation, eliminating 12 "
                    "incidents per year",
                    "Introduced progressive delivery, cutting failed "
                    "releases by 35%",
                    "Consolidated 5 alerting systems into 1 on-call workflow",
                ],
            },
            {
                "company": "Globex", "role": "SRE",
                "start_date": "Jun 2019", "end_date": "Feb 2022",
                "current": False,
                "bullets": [
                    "Migrated 30 services from EC2 to EKS with zero customer "
                    "downtime over 6 months",
                    "Delivered Prometheus dashboards adopted by 5 product "
                    "teams across the business",
                    "Reduced cloud spend by 18% through rightsizing and "
                    "scheduled shutdowns",
                    "Mentored 4 junior engineers through their first "
                    "production launches",
                    "Standardised 12 repositories onto a shared CI template",
                    "Resolved a recurring outage that had cost 3 hours of "
                    "downtime monthly",
                ],
            },
            {
                "company": "Initech", "role": "Platform Engineer",
                "start_date": "Jul 2017", "end_date": "May 2019",
                "current": False,
                "bullets": [
                    "Rearchitected the deployment pipeline, releasing 4 "
                    "times per week instead of once a month",
                    "Consolidated 9 legacy virtual machines onto 2 managed "
                    "clusters, saving 14 hours of upkeep monthly",
                    "Produced 15 runbooks that cut mean time to recovery by "
                    "40% across the platform team",
                    "Negotiated a 22% reduction in annual hosting costs with "
                    "the incumbent cloud vendor",
                    "Coached 6 developers to run their own deployments "
                    "safely against production",
                    "Established a change-review process covering every one "
                    "of the 30 weekly releases",
                ],
            },
        ],
        # 2026-10-25: projects is a SCORED 15-point category now (bullets,
        # metrics and verbs, share-graded like experience), so a "clean"
        # resume must have a non-empty, well-formed one.
        "projects": [
            {
                "name": "Pager tooling",
                "bullets": [
                    "Built a Go CLI that cut alert triage to 4 minutes for "
                    "2,400 developers",
                    "Shipped a Slack integration adopted by 300 teams in the "
                    "first quarter",
                ],
            },
            {
                "name": "Metrics sandbox",
                "bullets": [
                    "Automated Prometheus scenario replay, cutting repro "
                    "time by 80%",
                ],
            },
        ],
        "education": [{
            "school": "College of Engineering Pune", "degree": "BE",
            "field_of_study": "Computer Engineering",
            "start_date": "2014", "end_date": "2018",
        }],
        # 2026-10-25: a scored 30-point category -- 8+ items for full breadth,
        # every group labelled, no repeats.
        "skills": [
            {
                "label": "Infrastructure",
                "items": ["Kubernetes", "Terraform", "Prometheus", "AWS"],
            },
            {
                "label": "Languages & Data",
                "items": ["Python", "Go", "SQL", "Postgres"],
            },
        ],
    })


# ------------------------------------------------------- the declaration ---


@pytest.mark.parametrize("category", sorted(WEIGHTS))
def test_each_category_sub_budget_sums_to_its_weight(category):
    declared = sum(SUB_BUDGETS[category].values())
    assert declared == WEIGHTS[category], (
        f"{category}: checks declare {declared} points but the category is "
        f"worth {WEIGHTS[category]}. A new check must take its points from "
        f"inside the category, not add to the total."
    )


def test_the_weights_still_sum_to_one_hundred():
    assert sum(WEIGHTS.values()) == 100


def test_every_category_in_weights_has_a_declared_budget():
    assert set(SUB_BUDGETS) == set(WEIGHTS)


def test_no_check_is_worth_zero_points():
    """A 0-point check would render as a '+0 points' card, which looks broken.

    Part 3 rejected 0-point advisories for scored findings for this reason;
    unscored advisories are Tier 2 and are not Findings at all.
    """
    for category, checks in SUB_BUDGETS.items():
        for name, points in checks.items():
            assert points > 0, f"{category}.{name} is worth {points}"


# --------------------------------------------------------- the behaviour ---


def test_a_clean_resume_scores_one_hundred():
    assert score_resume(_clean())["overall_score"] == 100


@pytest.mark.parametrize("category", sorted(WEIGHTS))
def test_a_clean_resume_earns_the_full_weight_in_every_category(category):
    """Proves the declaration matches the CODE.

    If a scorer hands out fewer points than its category is worth, some
    check can never be satisfied and its card can never be cleared.
    """
    scored = score_resume(_clean())["category_scores"][category]
    assert scored["score"] == WEIGHTS[category]
    assert scored["max"] == WEIGHTS[category]


def test_a_clean_resume_raises_no_findings():
    assert score_resume(_clean())["findings"] == []


# ------------------------------------- the invariant, for the new findings ---
#
# (finding id, mutation) -- each introduces exactly one Tier 1 defect.
TIER_1_DEFECTS = [
    (
        "summary.filler_language",
        lambda r: r["summary"].__setitem__(
            "text", r["summary"]["text"] + " A proven track record."
        ),
    ),
    (
        "experience.filler_language",
        lambda r: r["experience"][0]["bullets"].__setitem__(
            0, "Led a team of 9 engineers, a results-driven team player"
        ),
    ),
    (
        "experience.impossible_metric",
        lambda r: r["experience"][0]["bullets"].__setitem__(
            0, "Reduced p95 latency by 100% across 40 services"
        ),
    ),
    (
        "experience.first_person",
        lambda r: r["experience"][0]["bullets"].__setitem__(
            # Keeps the strong opener so ONLY the pronoun rule fires --
            # "I led ..." would also (correctly) trip weak_verbs.
            0, "Led migration of 40 services for my team, cutting deploy "
               "time by 65%"
        ),
    ),
    (
        "experience.repeated_opener",
        lambda r: r["experience"][0].__setitem__("bullets", [
            "Led migration of 40 services, cutting deploy time by 65%",
            "Led the on-call rotation across an estate of 40 services",
            "Led 3 incident reviews that cut repeat outages by 30%",
            "Built Terraform modules adopted by 8 engineering teams",
        ]),
    ),
    (
        "experience.tense_mismatch",
        # Role 1 ended in Feb 2022, so a present-tense opener is wrong.
        lambda r: r["experience"][1]["bullets"].__setitem__(
            0, "Manage 30 services on EKS with zero customer downtime"
        ),
    ),
    (
        "summary.ai_language",
        # Short sentence appended: ai_language fires, complex_sentence must
        # not (4 words, one clause).
        lambda r: r["summary"].__setitem__(
            "text", r["summary"]["text"] + " Passionate about robust systems."
        ),
    ),
    (
        "summary.complex_sentence",
        # Clause-packed but metric-free, filler-free, verb-led: only the
        # complexity rule may trip.
        lambda r: r["summary"].__setitem__(
            "text",
            r["summary"]["text"]
            + " Coordinated releases across four teams, standardised tooling "
            "for the group, mentored six engineers, and kept on-call humane.",
        ),
    ),
    (
        "experience.ai_language",
        lambda r: r["experience"][0]["bullets"].__setitem__(
            # Keeps the credited opener and the metric so ONLY the lexicon
            # rule fires.
            0, "Led migration of 40 services, seamlessly cutting deploy time by 65%"
        ),
    ),
    (
        "experience.complex_sentence",
        lambda r: r["experience"][0]["bullets"].__setitem__(
            0,
            "Led a migration spanning 40 Kubernetes services across two "
            "regions, coordinated storage, networking, and compute cutovers, "
            "and cut deploy time by 65%",
        ),
    ),
]


@pytest.mark.parametrize(
    "finding_id,mutate", TIER_1_DEFECTS, ids=[c[0] for c in TIER_1_DEFECTS]
)
def test_each_tier_1_defect_raises_its_finding(finding_id, mutate):
    data = _clean()
    mutate(data)
    ids = [f["id"] for f in score_resume(data)["findings"]]
    assert finding_id in ids, ids


@pytest.mark.parametrize(
    "finding_id,mutate", TIER_1_DEFECTS, ids=[c[0] for c in TIER_1_DEFECTS]
)
def test_fixing_a_tier_1_defect_moves_the_score_by_exactly_its_points(
    finding_id, mutate
):
    """The card prints "+N points". The score must move by exactly N."""
    clean = _clean()
    broken = _clean()
    mutate(broken)

    before = score_resume(broken)
    finding = next(f for f in before["findings"] if f["id"] == finding_id)
    after = score_resume(clean)

    delta = after["overall_score"] - before["overall_score"]
    assert delta == finding["points"], (
        f"{finding_id} advertises {finding['points']} points but fixing it "
        f"moved the score by {delta}"
    )


@pytest.mark.parametrize(
    "finding_id,mutate", TIER_1_DEFECTS, ids=[c[0] for c in TIER_1_DEFECTS]
)
def test_a_tier_1_defect_introduces_only_its_own_finding(finding_id, mutate):
    """Isolation: if one mutation trips two rules, the rules overlap and the
    score double-counts a single defect."""
    data = _clean()
    mutate(data)
    ids = {f["id"] for f in score_resume(data)["findings"]}
    assert ids == {finding_id}, ids


#: finding id -> the SUB_BUDGETS key that funds it.
BUDGET_KEY = {
    "summary.filler_language": ("summary", "filler"),
    "summary.complex_sentence": ("summary", "complex_sentence"),
    "summary.ai_language": ("summary", "ai_language"),
    "experience.filler_language": ("experience", "filler"),
    "experience.impossible_metric": ("experience", "impossible_metric"),
    "experience.first_person": ("experience", "first_person"),
    "experience.repeated_opener": ("experience", "repeated_opener"),
    "experience.tense_mismatch": ("experience", "tense_mismatch"),
    "experience.complex_sentence": ("experience", "complex_sentence"),
    "experience.ai_language": ("experience", "ai_language"),
}


@pytest.mark.parametrize(
    "finding_id,mutate", TIER_1_DEFECTS, ids=[c[0] for c in TIER_1_DEFECTS]
)
def test_every_tier_1_finding_is_worth_its_declared_budget(finding_id, mutate):
    category, check = BUDGET_KEY[finding_id]
    data = _clean()
    mutate(data)
    finding = next(
        f for f in score_resume(data)["findings"] if f["id"] == finding_id
    )
    assert finding["points"] == SUB_BUDGETS[category][check]


def test_tier_1_findings_route_to_an_action_the_system_can_honour():
    """`quantify` for the metric rule, `rewrite` for the language rules.

    `impossible_metric` must NOT be `rewrite`: the correct figure is a fact
    only the candidate has, so it routes into the [__] placeholder path
    rather than letting the writer invent a replacement number.
    """
    expected = {
        "summary.filler_language": "rewrite",
        "summary.complex_sentence": "rewrite",
        "summary.ai_language": "rewrite",
        "experience.filler_language": "rewrite",
        "experience.impossible_metric": "quantify",
        "experience.first_person": "rewrite",
        "experience.repeated_opener": "rewrite",
        "experience.tense_mismatch": "rewrite",
        "experience.complex_sentence": "rewrite",
        "experience.ai_language": "rewrite",
    }
    for finding_id, mutate in TIER_1_DEFECTS:
        data = _clean()
        mutate(data)
        finding = next(
            f for f in score_resume(data)["findings"] if f["id"] == finding_id
        )
        assert finding["action"] == expected[finding_id], finding_id


def test_tier_1_findings_point_at_an_editable_target():
    """A container ref (`experience`) would 422 on every AI action.

    This is the `experience.no_bullets` lesson: that finding carries no
    action precisely because its target_ref is a container.
    """
    for finding_id, mutate in TIER_1_DEFECTS:
        data = _clean()
        mutate(data)
        finding = next(
            f for f in score_resume(data)["findings"] if f["id"] == finding_id
        )
        ref = finding["target_ref"]
        assert ref in ("summary.text",) or ".bullet_" in ref, (
            f"{finding_id} targets {ref!r}, which no AI action can resolve"
        )
