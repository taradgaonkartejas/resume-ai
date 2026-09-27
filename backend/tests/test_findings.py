"""Findings must be truthful and stable.

The guided editor prints "+N points" on every recommendation card. If fixing
the finding does not move the score by N, the product is lying to the user in
a way they will eventually notice (they fix three "+5" items and gain 7). That
is worse than showing no number, so the invariant is tested directly.
"""

import copy

import pytest

from app.services import heuristics, resume_ops


def _resume(**over) -> dict:
    base = {
        "contact": {
            "name": "Priya Sharma", "headline": "SRE",
            "email": "priya@example.com", "phone": "+91 90000 00000",
            "location": "Pune, IN", "links": ["github.com/priya"],
        },
        "summary": {"text": (
            "Site reliability engineer with six years running production "
            "infrastructure for high-traffic services. Led a migration that "
            "reduced deploy time by 65% and cut pager volume by 45%."
        )},
        "experience": [{
            "company": "Acme", "role": "Senior SRE", "dates": "2020 - Present",
            "bullets": [
                "Led migration of 40 services to Kubernetes, cutting deploy time by 65%",
                "Reduced pager volume by 45% by rewriting alert rules around SLOs",
            ],
        }],
        "projects": [],
        "education": [{"school": "COEP", "degree": "BE", "dates": "2014-2018"}],
        "skills": [{"label": "Infra", "items": ["Kubernetes", "Terraform"]}],
    }
    base.update(over)
    return base


def _score(data) -> int:
    return heuristics.score_resume(data)["overall_score"]


def _find(data, finding_id):
    for f in heuristics.score_resume(data)["findings"]:
        if f["id"] == finding_id:
            return f
    return None


# --------------------------------------------------------------- honesty ---
@pytest.mark.parametrize(
    "finding_id,break_it,fix_it",
    [
        (
            "contact.phone.missing",
            lambda d: d["contact"].update(phone=""),
            lambda d: d["contact"].update(phone="+91 90000 00000"),
        ),
        (
            "contact.email.invalid",
            lambda d: d["contact"].update(email="not-an-email"),
            lambda d: d["contact"].update(email="priya@example.com"),
        ),
        (
            "contact.name.missing",
            lambda d: d["contact"].update(name=""),
            lambda d: d["contact"].update(name="Priya Sharma"),
        ),
        (
            "contact.location.missing",
            lambda d: d["contact"].update(location=""),
            lambda d: d["contact"].update(location="Pune, IN"),
        ),
        (
            "contact.links.missing",
            lambda d: d["contact"].update(links=[]),
            lambda d: d["contact"].update(links=["github.com/priya"]),
        ),
        (
            "format.no_skills",
            lambda d: d.update(skills=[]),
            lambda d: d.update(skills=[{"label": "Infra", "items": ["Kubernetes"]}]),
        ),
        (
            "format.no_education",
            lambda d: d.update(education=[]),
            lambda d: d.update(education=[{"school": "COEP", "degree": "BE",
                                           "dates": "2014-2018"}]),
        ),
        (
            "experience.missing_dates",
            lambda d: d["experience"][0].update(dates=""),
            lambda d: d["experience"][0].update(dates="2020 - Present"),
        ),
    ],
)
def test_fixing_a_finding_moves_the_score_by_exactly_its_points(
    finding_id, break_it, fix_it
):
    broken = _resume()
    break_it(broken)

    finding = _find(broken, finding_id)
    assert finding is not None, f"{finding_id} was not reported when broken"

    before = _score(broken)
    fixed = copy.deepcopy(broken)
    fix_it(fixed)
    after = _score(fixed)

    assert after - before == finding["points"], (
        f"{finding_id} claims +{finding['points']} but the score moved "
        f"{after - before} ({before} -> {after})"
    )
    assert _find(fixed, finding_id) is None, "finding survived its own fix"


def test_summary_rewrite_claims_are_honest():
    """Summary findings interact, so verify the TOTAL claim, not one card."""
    broken = _resume(summary={"text": "Engineer."})
    before = _score(broken)
    claimed = sum(
        f["points"] for f in heuristics.score_resume(broken)["findings"]
        if f["category"] == "summary"
    )
    fixed = _resume()  # a good summary: long, quantified, action verb
    gained = _score(fixed) - before
    assert gained == claimed, (
        f"summary cards promise +{claimed} but fixing them all gained {gained}"
    )


def test_points_never_exceed_the_category_maximum():
    empty = {"contact": {}, "summary": {"text": ""}, "experience": [],
             "projects": [], "education": [], "skills": []}
    scored = heuristics.score_resume(empty)
    for name, cat in scored["category_scores"].items():
        claimed = sum(f["points"] for f in cat["findings"])
        headroom = cat["max"] - cat["score"]
        assert claimed <= headroom, (
            f"{name}: findings promise {claimed} but only {headroom} is available"
        )


def test_a_perfect_resume_has_no_findings_in_scored_categories():
    scored = heuristics.score_resume(_resume())
    for name in ("contact",):
        assert scored["category_scores"][name]["findings"] == []


# -------------------------------------------------------------- contract ---
def test_finding_shape_is_complete():
    scored = heuristics.score_resume({"contact": {}, "summary": {"text": ""},
                                      "experience": [], "projects": [],
                                      "education": [], "skills": []})
    required = {"id", "category", "target_ref", "severity", "points",
                "message", "fix_hint", "action", "meta"}
    for f in scored["findings"]:
        assert required <= set(f), f"missing {required - set(f)}"
        assert f["severity"] in {"high", "medium", "low"}
        assert f["points"] > 0, "a zero-point finding is noise"
        assert f["message"] and f["fix_hint"]


def test_notes_are_still_emitted_for_backward_compatibility():
    scored = heuristics.score_resume({"contact": {}, "summary": {"text": ""},
                                      "experience": [], "projects": [],
                                      "education": [], "skills": []})
    for cat in scored["category_scores"].values():
        assert cat["notes"] == [f["message"] for f in cat["findings"]]


def test_finding_ids_are_stable_across_runs():
    data = _resume(summary={"text": "Engineer."})
    first = [f["id"] for f in heuristics.score_resume(data)["findings"]]
    second = [f["id"] for f in heuristics.score_resume(data)["findings"]]
    assert first == second
    assert len(first) == len(set(first)), "duplicate finding ids"


def test_target_refs_resolve_or_name_a_section():
    """A card whose Fix button goes nowhere is a dead end."""
    from app.services import resume_ops

    data = _resume(summary={"text": "Engineer."})
    data["experience"][0]["bullets"].append("Worked on various tasks")
    sections = {"experience", "education", "skills", "projects",
                "contact.links", "contact.name", "contact.email",
                "contact.phone", "contact.location"}
    for f in heuristics.score_resume(data)["findings"]:
        ref = f["target_ref"]
        assert ref in sections or resume_ops.exists(data, ref), (
            f"{f['id']} points at unresolvable ref {ref!r}"
        )


# ----------------------------------------------------------------- steps ---
def test_build_steps_covers_every_category_in_order():
    steps = heuristics.build_steps(_resume())
    assert [s["id"] for s in steps] == [
        "contact", "summary", "experience", "format", "extras",
    ]
    for i, s in enumerate(steps):
        assert s["index"] == i
        assert s["title"] and s["description"]
        assert s["finding_count"] == len(s["findings"])
        assert s["points_available"] == sum(f["points"] for f in s["findings"])
        if s["id"] == "extras":
            continue
        assert s["max"] == heuristics.WEIGHTS[s["id"]]


def test_extras_step_is_navigable_but_carries_no_points():
    """It exists in the flow and is never scored -- both halves matter."""
    step = next(s for s in heuristics.build_steps(_resume()) if s["id"] == "extras")
    assert step["score"] == 0 and step["max"] == 0
    assert step["points_available"] == 0 and step["finding_count"] == 0
    assert step["findings"] == []
    # NOT "clear": a green tick would claim completion for a step nobody opened.
    assert step["status"] == "optional"
    assert "extras" not in heuristics.WEIGHTS


def test_extras_never_emit_findings_however_full_or_empty():
    for data in (_resume(), resume_ops.empty_resume()):
        rich = dict(data, extras=[{"kind": "certifications", "title": "Certifications",
                                   "entries": [{"primary": "AWS", "secondary": "",
                                                "date": "", "detail": ""}]}])
        for doc in (data, rich):
            findings = heuristics.score_resume(doc)["findings"]
            assert not [f for f in findings if f["category"] == "extras"]


def test_adding_extras_cannot_move_the_score():
    """The whole point of an unscored step. Asserted, not assumed."""
    base = _resume()
    before = heuristics.score_resume(base)["overall_score"]
    loaded = resume_ops.migrate(dict(base, extras=[
        {"kind": "certifications", "title": "Certifications",
         "entries": [{"primary": "AWS Solutions Architect", "secondary": "AWS",
                      "date": "2023", "detail": ""}]},
        {"kind": "languages", "title": "Languages",
         "entries": [{"primary": "Hindi", "secondary": "Native",
                      "date": "", "detail": ""}]},
    ]))
    assert heuristics.score_resume(loaded)["overall_score"] == before


def test_step_status_reflects_findings():
    clean = heuristics.build_steps(_resume())
    contact_step = next(s for s in clean if s["id"] == "contact")
    assert contact_step["status"] == "clear"
    assert contact_step["finding_count"] == 0

    broken = _resume()
    broken["contact"]["email"] = ""
    broken["contact"]["phone"] = ""
    step = next(s for s in heuristics.build_steps(broken) if s["id"] == "contact")
    assert step["status"] == "attention"
    assert step["points_available"] == 7


def test_steps_reuse_a_supplied_score_without_recomputing():
    data = _resume()
    scored = heuristics.score_resume(data)
    steps = heuristics.build_steps(data, scored)
    assert sum(s["score"] for s in steps) == scored["overall_score"]


# --------------------------------------------- every rewrite ref must resolve
def test_every_rewrite_finding_points_at_resolvable_text():
    """An AI action on an unresolvable ref would 422 on every click.

    `experience.no_bullets` used to declare action="rewrite" with
    target_ref="experience", which is a container. resolve() raises on it.
    """
    from app.services import resume_ops
    from app.services.heuristics import (
        _score_contact,
        _score_experience,
        _score_format,
        _score_summary,
    )
    from app.services.parsing import empty_resume

    samples = []

    blank = empty_resume()
    blank["contact"] = {"name": "", "email": "bad", "phone": "", "location": "", "links": []}
    blank["summary"] = {"text": ""}
    blank["experience"] = [{"company": "Acme", "role": "Eng", "dates": "", "bullets": []}]
    samples.append(blank)

    thin = empty_resume()
    thin["contact"] = {
        "name": "Jane", "email": "j@x.com", "phone": "555-0100",
        "location": "Pune", "links": ["gh"],
    }
    thin["summary"] = {"text": "Engineer who worked on things for a while in roles."}
    thin["experience"] = [
        {
            "company": "Acme", "role": "Eng", "dates": "2020-2024",
            "bullets": ["Worked on the platform", "Helped with deploys " + ("word " * 50)],
        }
    ]
    thin["skills"] = [{"label": "P", "items": ["Python"]}]
    thin["education"] = [{"school": "S", "degree": "B", "dates": ""}]
    samples.append(thin)

    checked = 0
    for data in samples:
        for scorer in (_score_contact, _score_summary, _score_experience, _score_format):
            for finding in scorer(data)[1]:
                if finding.action != "rewrite":
                    continue
                checked += 1
                assert resume_ops.exists(data, finding.target_ref), (
                    f"{finding.id} has action='rewrite' but target_ref "
                    f"{finding.target_ref!r} does not resolve"
                )
    assert checked >= 6, f"expected several rewrite findings, saw {checked}"


def test_no_bullets_has_no_ai_action():
    from app.services.heuristics import _score_experience
    from app.services.parsing import empty_resume

    data = empty_resume()
    data["experience"] = [{"company": "Acme", "role": "Eng", "dates": "", "bullets": []}]
    found = [f for f in _score_experience(data)[1] if f.id == "experience.no_bullets"]
    assert found and found[0].action == ""
