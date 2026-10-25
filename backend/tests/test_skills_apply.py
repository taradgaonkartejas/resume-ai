"""Missing skills can be applied -- but only the ones the user picked.

The user asked for the skills answer to be applicable, reversing the earlier
advisory-only decision. The reason behind that decision does not go away:
a skill from a job description is, by definition, not in the resume, so
nothing in the grounding machinery can verify it. The critic compares a
draft against existing text; here there is no existing text to compare to.

So the apply path routes through the SAME mechanism the clarifying-question
flow uses -- `needs_input` -> `answer` -> `attested` -- which exists precisely
because `answer_question` is, in its own words, "the one place in the system
where something enters a resume that the resume did not already support".

What these tests pin:

  * only offered keywords can be applied -- a client cannot post its own list
  * only SELECTED keywords land; the rest never touch the resume
  * declining is a clean rejection, not an error
  * the version row says `attested`, never `chat`, so "did a model write
    this?" stays answerable forever
  * skills join the group the user's own resume says they belong to
"""

from __future__ import annotations

import uuid as _uuid

import pytest

from app.models import JobDescription, ResumeVersion, Suggestion, TailoringSession
from tests.conftest import new_resume

SAMPLE = {
    "contact": {
        "name": "Sam Rivera",
        "headline": "Engineer",
        "email": "s@x.io",
        "phone": "+1 555 0100",
        "location": "Pune",
        "links": [],
    },
    "summary": {"text": "Engineer with six years on cloud infrastructure."},
    "experience": [
        {
            "company": "Acme",
            "role": "Engineer",
            "dates": "2020 - Present",
            "bullets": ["Cut build times from 20 minutes to 6 minutes."],
        }
    ],
    "projects": [],
    "education": [],
    "skills": [
        {"label": "Languages", "items": ["Java", "SQL"]},
        {"label": "Cloud", "items": ["AWS"]},
    ],
}

GAPS = ["java8/17/21", "springboot3", "activemq", "gitlab ci/cd"]


@pytest.fixture
def card(client, priya):
    """A resume with a tailoring session, and the skills question card."""
    resume_id = new_resume(client, priya, "Skills", dict(SAMPLE))
    db = client._session_factory()
    jd = JobDescription(title="Java Dev", content=" ".join(GAPS))
    db.add(jd)
    db.flush()
    db.add(
        TailoringSession(
            resume_id=_uuid.UUID(str(resume_id)),
            job_description_id=jd.id,
            match_percent=10.0,
            baseline_percent=5.0,
            matched_keywords=["java"],
            gap_keywords=list(GAPS),
            thread_id="t",
            graph_state="done",
        )
    )
    db.commit()
    db.close()

    r = client.post(
        f"/api/resumes/{resume_id}/chat",
        json={"content": "Suggest skills I am missing"},
        headers={"X-User-Id": priya},
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["suggestion"] is not None, "no skills card was offered"
    return resume_id, body["suggestion"]


def _answer(client, user_id, sid, text):
    r = client.post(
        f"/api/suggestions/{sid}/answer",
        json={"answer": text, "confirmed": True},
        headers={"X-User-Id": user_id},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _skills(client, user_id, resume_id) -> dict[str, list[str]]:
    data = client.get(
        f"/api/resumes/{resume_id}", headers={"X-User-Id": user_id}
    ).json()["structured_data"]
    return {g["label"]: list(g["items"]) for g in data["skills"]}


# ---------------------------------------------------------------- the card


def test_the_answer_is_a_question_card_not_an_ai_draft(client, priya, card):
    _, suggestion = card
    assert suggestion["status"] == "needs_input", (
        "an applicable skills card must be a QUESTION until the user answers; "
        "a pending draft would be the model asserting skills on their behalf"
    )
    assert suggestion["suggested_text"] == ""
    assert suggestion["section"] == "skills"
    assert set(suggestion["keywords"]) == set(GAPS)


def test_asking_twice_refreshes_one_card_rather_than_stacking(client, priya, card):
    resume_id, first = card
    client.post(
        f"/api/resumes/{resume_id}/chat",
        json={"content": "Suggest skills I am missing"},
        headers={"X-User-Id": priya},
    )
    db = client._session_factory()
    try:
        open_cards = (
            db.query(Suggestion)
            .filter(
                Suggestion.resume_id == _uuid.UUID(str(resume_id)),
                Suggestion.section == "skills",
                Suggestion.status == "needs_input",
            )
            .count()
        )
    finally:
        db.close()
    assert open_cards == 1, f"{open_cards} open skills cards all writing to one section"
    assert first["id"]


# --------------------------------------------------------------- selection


def test_only_the_selected_skills_are_added(client, priya, card):
    resume_id, suggestion = card
    _answer(client, priya, suggestion["id"], "java8/17/21, activemq")

    after = _skills(client, priya, resume_id)
    flat = [i for items in after.values() for i in items]
    assert "java8/17/21" in flat
    assert "activemq" in flat
    assert "springboot3" not in flat, "an unselected skill reached the resume"
    assert "gitlab ci/cd" not in flat, "an unselected skill reached the resume"


def test_a_client_cannot_add_a_skill_that_was_never_offered(client, priya, card):
    """The card's keyword list is the server's, and it is the whole menu."""
    resume_id, suggestion = card
    _answer(client, priya, suggestion["id"], "java8/17/21, Kubernetes, PhD in Physics")

    flat = [i for items in _skills(client, priya, resume_id).values() for i in items]
    assert "java8/17/21" in flat
    assert "Kubernetes" not in flat, "a skill the server never offered was applied"
    assert "PhD in Physics" not in flat


def test_declining_rejects_the_card_and_changes_nothing(client, priya, card):
    resume_id, suggestion = card
    before = _skills(client, priya, resume_id)

    out = _answer(client, priya, suggestion["id"], "")
    assert out["status"] == "rejected", "declining must be a clean outcome, not an error"
    assert _skills(client, priya, resume_id) == before


def test_a_skill_already_present_is_not_duplicated(client, priya, card):
    resume_id, suggestion = card
    db = client._session_factory()
    row = db.query(Suggestion).filter(Suggestion.id == _uuid.UUID(suggestion["id"])).first()
    row.keywords = ["Java", "activemq"]
    db.commit()
    db.close()

    _answer(client, priya, suggestion["id"], "Java, activemq")
    languages = _skills(client, priya, resume_id)["Languages"]
    assert languages.count("Java") == 1, f"duplicated: {languages}"


# -------------------------------------------------------------- provenance


def test_the_applied_skills_are_recorded_as_the_users_own(client, priya, card):
    """"Did a model write this?" must stay answerable from the version log."""
    resume_id, suggestion = card
    out = _answer(client, priya, suggestion["id"], "activemq")
    assert out["attested"] is True
    assert out["status"] == "accepted"

    db = client._session_factory()
    try:
        latest = (
            db.query(ResumeVersion)
            .filter(ResumeVersion.resume_id == _uuid.UUID(str(resume_id)))
            .order_by(ResumeVersion.created_at.desc())
            .first()
        )
    finally:
        db.close()
    assert latest.change_source == "attested", (
        f"skills the user asserted were logged as {latest.change_source!r}; "
        "that makes a user's own claim indistinguishable from an AI rewrite"
    )


def test_the_answer_is_stored_verbatim_for_audit(client, priya, card):
    _, suggestion = card
    out = _answer(client, priya, suggestion["id"], "java8/17/21, activemq")
    assert out["answer"] == "java8/17/21, activemq"


# ------------------------------------------------------------- group match


def test_a_skill_joins_the_group_the_resume_says_it_belongs_to(client, priya, card):
    """Matched on the user's own wording, not a hard-coded taxonomy."""
    resume_id, suggestion = card
    _answer(client, priya, suggestion["id"], "java8/17/21")
    after = _skills(client, priya, resume_id)
    assert "java8/17/21" in after["Languages"], (
        f"expected it beside the existing Java entry, got {after}"
    )
    assert "Additional Skills" not in after


def test_an_unmatchable_skill_goes_to_a_new_group_rather_than_a_wrong_one(
    client, priya, card
):
    resume_id, suggestion = card
    _answer(client, priya, suggestion["id"], "gitlab ci/cd")
    after = _skills(client, priya, resume_id)
    assert after.get("Additional Skills") == ["gitlab ci/cd"]
    assert after["Languages"] == ["Java", "SQL"], "it was filed under the wrong group"


def test_applying_skills_shows_up_in_the_chat_transcript(client, priya, card):
    """Same mirroring contract as every other edit."""
    resume_id, suggestion = card
    _answer(client, priya, suggestion["id"], "activemq")

    messages = client.get(
        f"/api/resumes/{resume_id}/chat", headers={"X-User-Id": priya}
    ).json()["messages"]
    events = [m for m in messages if m["role"] == "event"]
    assert events, "the resume changed and the transcript did not say so"
    assert events[-1]["meta"]["kind"] == "suggestion_accepted"


def test_the_card_can_be_read_back_by_id(client, priya, card):
    """Chat stores only an id; without this the picker is unreachable."""
    _, suggestion = card
    r = client.get(f"/api/suggestions/{suggestion['id']}", headers={"X-User-Id": priya})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "needs_input"
    assert body["section"] == "skills"
    assert set(body["keywords"]) == set(GAPS)


def test_another_user_cannot_read_the_card(client, priya, arjun, card):
    _, suggestion = card
    r = client.get(f"/api/suggestions/{suggestion['id']}", headers={"X-User-Id": arjun})
    assert r.status_code == 404, (
        "a new read endpoint must honour the same per-user scoping as the rest"
    )


def test_accepting_a_question_card_is_still_refused(client, priya, card):
    """The picker exists because this path raises -- keep it raising."""
    _, suggestion = card
    r = client.patch(
        f"/api/suggestions/{suggestion['id']}",
        json={"action": "accept"},
        headers={"X-User-Id": priya},
    )
    assert r.status_code >= 400
