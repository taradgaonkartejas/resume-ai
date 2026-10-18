"""PLAN-8 Increment 6 — the clarifying-question flow.

This is the only feature that lets something into a resume which the resume
did not already support, so the tests are weighted towards the ways that
could go wrong rather than the happy path.
"""

import uuid

import pytest

from app.models import Suggestion
from app.services.clarifying import (
    MAX_QUESTIONS,
    answer_supports,
    is_skill,
    question_for,
    questions_for_gaps,
)

JD = """Senior Site Reliability Engineer

Responsibilities:
- Operate and scale Kubernetes clusters across multiple AWS regions
- Experience with GitOps and progressive delivery
- 3+ years with Redis or a comparable caching layer
"""

RESUME = {
    "contact": {"name": "Priya Sharma", "email": "p@example.com",
                "phone": "+91 98765 43210", "location": "Pune", "links": []},
    "summary": {"text": "SRE with eight years on container platforms."},
    "experience": [{
        "company": "Acme", "role": "SRE", "dates": "2020 - Present",
        "bullets": ["Cut deploy time by 65% across 40 services."],
    }],
    "projects": [], "education": [],
    "skills": [{"label": "Infrastructure", "items": ["Kubernetes", "Docker"]}],
}


# ------------------------------------------------------------ the skill filter
def test_generic_job_description_words_are_not_skills():
    """The filter the whole feature's credibility rests on.

    `extract_keywords` is a word counter, so a real job description produces
    these as "gaps" alongside the genuine ones. Asking "do you have
    experience with 'across'?" would discredit the feature on first use.
    """
    for junk in ["across", "define", "looking", "own", "mean", "drive",
                 "requirements", "responsibilities", "improve"]:
        assert not is_skill(junk), f"{junk!r} should not produce a question"


def test_taxonomy_members_are_skills():
    for real in ["gitops", "redis", "kubernetes", "terraform", "agile"]:
        assert is_skill(real), f"{real!r} should produce a question"


def test_an_alias_is_a_skill_too():
    """`k8s` is how a job description often spells it."""
    assert is_skill("k8s")
    assert is_skill("argocd")


# --------------------------------------------------------- duration detection
def test_a_duration_on_the_next_line_does_not_attach():
    """Regression: the first implementation used a +/-60 character window.

    Given the two adjacent bullets in JD above, it reached across the
    newline and reported that the job wanted "3+ years of GitOps" -- a
    requirement the employer never wrote, shown to the user as a quote from
    the posting. Requirements are written one per bullet.
    """
    gitops = question_for("gitops", JD)
    assert gitops["requires_text"] is False
    assert "3+ years" not in gitops["question"]


def test_a_duration_on_the_same_line_does_attach():
    redis = question_for("redis", JD)
    assert redis["requires_text"] is True
    assert "3+ years" in redis["question"]


def test_the_duration_is_quoted_from_the_posting_not_invented():
    assert "3+ years" in question_for("redis", JD)["question"]
    assert "5" not in question_for("redis", JD)["question"]


# ------------------------------------------------------------ question wording
def test_the_question_names_the_do_nothing_option():
    """A question that only offers "yes" is not a question.

    The reference's "Do you have experience utilizing X in a professional
    environment?" prints its preferred answer on itself. Saying no has to be
    as easy, and as explicitly permitted, as saying yes.
    """
    for keyword in ["gitops", "redis"]:
        text = question_for(keyword, JD)["question"].lower()
        assert "leave" in text and "blank" in text
        assert "reject" in text


def test_the_question_asks_where_not_whether():
    assert "say where" in question_for("gitops", JD)["question"]


def test_the_canonical_spelling_is_used():
    """The card says GitOps, not the job description's lowercase token."""
    assert question_for("gitops", JD)["label"] == "GitOps"


# -------------------------------------------------------------- the gap set
def test_only_skill_like_gaps_become_questions():
    gaps = ["across", "gitops", "define", "redis", "looking"]
    labels = [q["label"] for q in questions_for_gaps(gaps, JD)]
    assert sorted(labels) == ["GitOps", "Redis"]


def test_duration_qualified_questions_come_first():
    """The employer named a bar for these, so the gap is explicit."""
    out = questions_for_gaps(["gitops", "redis"], JD)
    assert out[0]["label"] == "Redis"


def test_two_spellings_of_one_skill_ask_once():
    """A JD saying both "ci" and "pipelines" is one question, not two."""
    out = questions_for_gaps(["ci", "pipelines", "cd"], JD)
    assert len(out) == 1


def test_the_question_list_is_capped():
    gaps = ["gitops", "redis", "agile", "terraform", "docker", "python", "go"]
    assert len(questions_for_gaps(gaps, JD)) == MAX_QUESTIONS


def test_no_gaps_means_no_questions():
    assert questions_for_gaps([], JD) == []


# ------------------------------------------------------------ answer support
def test_an_answer_naming_the_skill_supports_it():
    assert answer_supports("I ran GitOps at Acme for two years", "gitops")


def test_an_alias_in_the_answer_supports_it():
    """The user says ArgoCD; the job description said GitOps."""
    assert answer_supports("Set up ArgoCD for the platform team", "gitops")


def test_a_blank_or_unrelated_answer_supports_nothing():
    assert not answer_supports("", "gitops")
    assert not answer_supports("   ", "gitops")
    assert not answer_supports("no", "gitops")
    assert not answer_supports("I have done a lot of infrastructure work", "gitops")


# ------------------------------------------------------------------- service
def _question_card(client, resume_id, *, keyword="gitops", requires_text=False):
    db = client._session_factory()
    try:
        row = Suggestion(
            resume_id=uuid.UUID(resume_id),
            origin="tailor",
            section="skills",
            target_ref="",
            keywords=[keyword],
            status="needs_input",
            grounded=False,
            question=question_for(keyword, JD)["question"],
            requires_text=requires_text,
        )
        db.add(row)
        db.commit()
        return str(row.id)
    finally:
        db.close()


@pytest.fixture
def resume_id(client, priya):
    from tests.conftest import new_resume
    return new_resume(client, priya, "Clarify", RESUME)


def test_a_supporting_answer_becomes_a_pending_suggestion(client, priya, resume_id):
    sid = _question_card(client, resume_id)
    out = client.post(
        f"/api/suggestions/{sid}/answer",
        json={"answer": "Ran ArgoCD on the platform team"},
        headers={"X-User-Id": priya},
    )
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["status"] == "pending"
    assert body["attested"] is True
    # The crux: a user-attested fact is NOT grounded, and the two flags must
    # stay distinguishable after the answer lands.
    assert body["grounded"] is False
    assert body["suggested_text"] == "Kubernetes, Docker, GitOps"
    assert body["target_ref"] == "skills.0.items"


def test_the_answer_is_stored_verbatim(client, priya, resume_id):
    sid = _question_card(client, resume_id)
    typed = "Ran ArgoCD on the platform team, 2021-2023"
    body = client.post(
        f"/api/suggestions/{sid}/answer",
        json={"answer": typed},
        headers={"X-User-Id": priya},
    ).json()
    assert body["answer"] == typed


def test_a_declined_question_is_rejected_not_softened(client, priya, resume_id):
    """The failure this feature exists to avoid.

    The user says no and a suggestion reading "familiar with GitOps" appears
    anyway. There must be no draft at all.
    """
    sid = _question_card(client, resume_id)
    body = client.post(
        f"/api/suggestions/{sid}/answer",
        json={"answer": "no, never used it"},
        headers={"X-User-Id": priya},
    ).json()
    assert body["status"] == "rejected"
    assert body["suggested_text"] == ""
    assert body["attested"] is False
    # Still recorded: "asked and declined" is worth keeping.
    assert body["answer"] == "no, never used it"


def test_a_blank_answer_is_a_decline(client, priya, resume_id):
    sid = _question_card(client, resume_id)
    body = client.post(
        f"/api/suggestions/{sid}/answer", json={"answer": "   "},
        headers={"X-User-Id": priya},
    ).json()
    assert body["status"] == "rejected"


def test_bare_yes_is_enough_for_a_plain_tool_question(client, priya, resume_id):
    sid = _question_card(client, resume_id, requires_text=False)
    body = client.post(
        f"/api/suggestions/{sid}/answer", json={"confirmed": True},
        headers={"X-User-Id": priya},
    ).json()
    assert body["status"] == "pending"
    assert body["attested"] is True


def test_bare_yes_is_refused_when_the_job_named_a_duration(client, priya, resume_id):
    """Pressing a button cannot attest "3+ years".

    The number would be written into the resume as the user's own word when
    the user never typed a number at all.
    """
    sid = _question_card(client, resume_id, keyword="redis", requires_text=True)
    out = client.post(
        f"/api/suggestions/{sid}/answer", json={"confirmed": True},
        headers={"X-User-Id": priya},
    )
    assert out.status_code == 422
    db = client._session_factory()
    try:
        assert db.get(Suggestion, uuid.UUID(sid)).status == "needs_input"
    finally:
        db.close()


def test_text_satisfies_a_duration_question(client, priya, resume_id):
    sid = _question_card(client, resume_id, keyword="redis", requires_text=True)
    body = client.post(
        f"/api/suggestions/{sid}/answer",
        json={"answer": "Redis cache at Acme, four years"},
        headers={"X-User-Id": priya},
    ).json()
    assert body["status"] == "pending"
    assert body["suggested_text"] == "Kubernetes, Docker, Redis"


# ------------------------------------------------------------ state machine
def test_a_question_cannot_be_accepted_directly(client, priya, resume_id):
    """There is no draft to apply -- suggested_text is "".

    Without this guard, accept() would reach apply_patch with an empty
    target_ref and the failure would surface as something unrelated.
    """
    sid = _question_card(client, resume_id)
    out = client.patch(
        f"/api/suggestions/{sid}", json={"action": "accept"},
        headers={"X-User-Id": priya},
    )
    # 422, matching the double-accept contract pinned in test_lifecycle.py:
    # InvalidStateTransition subclasses ValidationError. Arguably this family
    # is a 409 by ConflictError's own docstring ("nothing about the request
    # is wrong, it conflicts with current state") -- but re-basing a shared
    # exception is a separate change, and one state conflict answering 409
    # while its neighbour answers 422 would be worse than either.
    assert out.status_code == 422


def test_a_question_can_be_rejected(client, priya, resume_id):
    sid = _question_card(client, resume_id)
    out = client.patch(
        f"/api/suggestions/{sid}", json={"action": "reject"},
        headers={"X-User-Id": priya},
    )
    assert out.status_code == 200
    assert out.json()["status"] == "rejected"


def test_answering_twice_is_refused(client, priya, resume_id):
    sid = _question_card(client, resume_id)
    first = client.post(
        f"/api/suggestions/{sid}/answer", json={"confirmed": True},
        headers={"X-User-Id": priya},
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/suggestions/{sid}/answer", json={"confirmed": True},
        headers={"X-User-Id": priya},
    )
    assert second.status_code == 422  # see the note above


def test_another_user_cannot_answer_your_question(client, priya, arjun, resume_id):
    sid = _question_card(client, resume_id)
    out = client.post(
        f"/api/suggestions/{sid}/answer", json={"confirmed": True},
        headers={"X-User-Id": arjun},
    )
    assert out.status_code == 404


def test_a_skill_that_arrived_meanwhile_is_not_added_twice(client, priya, resume_id):
    sid = _question_card(client, resume_id, keyword="docker")
    body = client.post(
        f"/api/suggestions/{sid}/answer",
        json={"answer": "Yes, Docker daily"},
        headers={"X-User-Id": priya},
    ).json()
    assert body["status"] == "rejected"
    assert "already listed" in body["critic_notes"]


# ---------------------------------------------------------------- provenance
def test_accepting_an_attested_card_is_logged_as_attested(client, priya, resume_id):
    """The audit trail must outlive the suggestion row.

    A year later, "did this line come from the resume or from a box someone
    typed into?" has to be answerable, and the version log is the only
    record that survives.
    """
    sid = _question_card(client, resume_id)
    client.post(
        f"/api/suggestions/{sid}/answer", json={"confirmed": True},
        headers={"X-User-Id": priya},
    )
    accepted = client.patch(
        f"/api/suggestions/{sid}", json={"action": "accept"},
        headers={"X-User-Id": priya},
    )
    assert accepted.status_code == 200

    from app.models import ResumeVersion
    db = client._session_factory()
    try:
        latest = (
            db.query(ResumeVersion)
            .filter(ResumeVersion.resume_id == uuid.UUID(resume_id))
            .order_by(ResumeVersion.seq.desc())
            .first()
        )
        assert latest.change_source == "attested"
        assert "GitOps" in latest.snapshot["skills"][0]["items"]
    finally:
        db.close()


def test_an_ordinary_suggestion_is_not_logged_as_attested(client, priya, resume_id):
    """Guard the guard: the provenance label must actually discriminate."""
    db = client._session_factory()
    try:
        row = Suggestion(
            resume_id=uuid.UUID(resume_id), origin="tailor", section="skills",
            target_ref="skills.0.items", original_text="Kubernetes, Docker",
            suggested_text="Kubernetes, Docker, Helm", status="pending",
            grounded=True,
        )
        db.add(row)
        db.commit()
        sid = str(row.id)
    finally:
        db.close()

    client.patch(f"/api/suggestions/{sid}", json={"action": "accept"},
                 headers={"X-User-Id": priya})

    from app.models import ResumeVersion
    db = client._session_factory()
    try:
        latest = (
            db.query(ResumeVersion)
            .filter(ResumeVersion.resume_id == uuid.UUID(resume_id))
            .order_by(ResumeVersion.seq.desc())
            .first()
        )
        assert latest.change_source == "tailor"
    finally:
        db.close()


def test_a_resume_with_no_skills_section_says_so(client, priya):
    from tests.conftest import new_resume
    data = {**RESUME, "skills": []}
    rid = new_resume(client, priya, "No skills", data)
    sid = _question_card(client, rid)
    out = client.post(
        f"/api/suggestions/{sid}/answer", json={"confirmed": True},
        headers={"X-User-Id": priya},
    )
    assert out.status_code == 422
    assert "skills section" in out.json()["detail"]


# ------------------------------------------------------------------ buckets
def test_questions_appear_in_the_active_bucket_first(client, priya, resume_id):
    """Regression: the three status filters were exhaustive over the OLD
    three statuses, so a `needs_input` row matched none of them and the
    card was silently invisible -- server state the UI never shows, which
    is the same class of bug the bulk-format panel hit.
    """
    from app.models import JobDescription, TailoringSession

    db = client._session_factory()
    try:
        jd = JobDescription(title="SRE", content=JD, extracted_keywords=["gitops"])
        db.add(jd)
        db.flush()
        session = TailoringSession(
            resume_id=uuid.UUID(resume_id), job_description_id=jd.id,
        )
        db.add(session)
        db.flush()
        sid = str(session.id)
        db.add(Suggestion(
            resume_id=uuid.UUID(resume_id), session_id=session.id,
            section="experience", target_ref="experience.0.bullets.0",
            original_text="Cut deploy time by 65% across 40 services.",
            suggested_text="Cut deploy time by 65% across 40 services, on Kubernetes.",
            status="pending", grounded=True,
        ))
        db.add(Suggestion(
            resume_id=uuid.UUID(resume_id), session_id=session.id,
            section="skills", target_ref="", keywords=["gitops"],
            status="needs_input", grounded=False,
            question=question_for("gitops", JD)["question"],
        ))
        db.commit()
    finally:
        db.close()

    body = client.get(
        f"/api/resumes/{resume_id}/tailor/{sid}/suggestions", headers={"X-User-Id": priya}
    ).json()
    statuses = [s["status"] for s in body["active"]]
    assert "needs_input" in statuses, "question card vanished from every bucket"
    assert statuses[0] == "needs_input", "questions should lead the active list"
