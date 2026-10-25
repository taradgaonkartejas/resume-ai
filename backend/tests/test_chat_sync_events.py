"""Edits made in the Analyze/Tailor panes must appear in the chat transcript.

Before this, the sync ran one way only. Chat suggestions reached the Analyze
pane (they share the accept mutation, which invalidates the analysis), but
nothing travelled back: a user could accept six rewrites in the Tailor pane
and the conversation would still end at the last sentence they typed.

Three properties are load-bearing and none of them is obvious from reading
the happy path, so each gets a test:

  1. Mirrored rows are `event`, never `assistant`. The rule at the top of
     ChatPane is that the assistant never claims to have edited the resume.
     An event that borrowed the assistant role would make exactly that claim,
     and it would be a lie -- the user applied the edit, not the model.

  2. Events do not spend chat tokens. The allowance is 25 MESSAGES and
     `ChatService.send` decrements one per call. If mirroring went through
     the same door, twenty accepts in the Tailor pane would silently consume
     most of a budget the user thinks belongs to conversation.

  3. The score card appears only when the score MOVED. `score_delta == 0`
     is the signal the pane uses to suppress it.
"""

from __future__ import annotations

import uuid as _uuid

from app.models import ChatMessage, JobDescription, Suggestion, TailoringSession
from app.services.heuristics import score_resume
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
    "summary": {
        "text": "Platform engineer with six years building and running "
        "cloud infrastructure for high-traffic services."
    },
    "experience": [
        {
            "company": "Acme",
            "role": "Engineer",
            "dates": "2020 - Present",
            "bullets": [
                "Responsible for the deployment pipeline and its upkeep",
                "Cut build times from 20 minutes to 6 minutes.",
            ],
        }
    ],
    "projects": [],
    "education": [],
    "skills": [{"label": "Languages", "items": ["Python", "SQL"]}],
}


def _suggestion(client, user_id, suggested: str, ref: str = "exp_0.bullet_0"):
    """A pending tailor suggestion sitting on a real resume."""
    resume_id = new_resume(client, user_id, "Mirrored", dict(SAMPLE))
    db = client._session_factory()
    jd = JobDescription(title="SRE", content="kubernetes terraform")
    db.add(jd)
    db.flush()
    sess = TailoringSession(
        resume_id=_uuid.UUID(str(resume_id)),
        job_description_id=jd.id,
        match_percent=0.0,
        baseline_percent=0.0,
        matched_keywords=[],
        gap_keywords=["kubernetes"],
        thread_id="t",
        graph_state="done",
    )
    db.add(sess)
    db.flush()
    sug = Suggestion(
        session_id=sess.id,
        resume_id=_uuid.UUID(str(resume_id)),
        origin="tailor",
        section="experience",
        target_ref=ref,
        placement="Engineer - Acme",
        original_text=SAMPLE["experience"][0]["bullets"][0],
        suggested_text=suggested,
        edited_text="",
        keywords=["kubernetes"],
        reasoning="r",
        status="pending",
        grounded=True,
        critic_notes="",
        revisions=0,
    )
    db.add(sug)
    db.commit()
    out = (resume_id, str(sug.id))
    db.close()
    return out


def _events(client, resume_id) -> list[ChatMessage]:
    db = client._session_factory()
    try:
        return (
            db.query(ChatMessage)
            .filter(
                ChatMessage.resume_id == _uuid.UUID(str(resume_id)),
                ChatMessage.role == "event",
            )
            .order_by(ChatMessage.created_at)
            .all()
        )
    finally:
        db.close()


def _accept(client, user_id, suggestion_id):
    r = client.patch(
        f"/api/suggestions/{suggestion_id}",
        json={"action": "accept"},
        headers={"X-User-Id": user_id},
    )
    assert r.status_code == 200, r.text
    return r


# ---------------------------------------------------------------- property 1


def test_accepting_a_suggestion_writes_one_event_to_the_transcript(client, priya):
    resume_id, sid = _suggestion(
        client, priya, "Rebuilt the deployment pipeline, cutting failures by 35 percent."
    )
    assert _events(client, resume_id) == [], "transcript dirty before the accept"

    _accept(client, priya, sid)

    rows = _events(client, resume_id)
    assert len(rows) == 1, f"expected exactly one mirrored row, got {len(rows)}"
    assert rows[0].meta.get("kind") == "suggestion_accepted"
    assert str(rows[0].suggestion_id) == sid


def test_a_mirrored_edit_is_never_attributed_to_the_assistant(client, priya):
    """The assistant must not be shown as the author of a user's edit."""
    resume_id, sid = _suggestion(
        client, priya, "Rebuilt the deployment pipeline, cutting failures by 35 percent."
    )
    _accept(client, priya, sid)

    db = client._session_factory()
    try:
        roles = [
            m.role
            for m in db.query(ChatMessage)
            .filter(ChatMessage.resume_id == _uuid.UUID(str(resume_id)))
            .all()
        ]
    finally:
        db.close()
    assert "assistant" not in roles, (
        "a mirrored edit was written as assistant prose; ChatPane's standing "
        "rule is that the assistant never claims to have changed the resume"
    )
    assert roles == ["event"]


def test_the_edit_still_lands_even_if_the_transcript_write_fails(client, priya, monkeypatch):
    """Mirroring is a log. A broken log must never cost the user their edit."""
    from app.repositories.chat_repository import ChatRepository

    def boom(*a, **k):
        raise RuntimeError("transcript unavailable")

    monkeypatch.setattr(ChatRepository, "append_event", boom)

    resume_id, sid = _suggestion(
        client, priya, "Rebuilt the deployment pipeline, cutting failures by 35 percent."
    )
    _accept(client, priya, sid)

    r = client.get(f"/api/resumes/{resume_id}", headers={"X-User-Id": priya})
    bullet = r.json()["structured_data"]["experience"][0]["bullets"][0]
    assert "Rebuilt" in bullet, "the accept was rolled back by a failed log write"


# ---------------------------------------------------------------- property 2


def test_mirrored_events_do_not_spend_the_chat_allowance(client, priya):
    """Twenty accepts in the Tailor pane must not eat the chat budget."""
    resume_id, sid = _suggestion(
        client, priya, "Rebuilt the deployment pipeline, cutting failures by 35 percent."
    )
    before = client.get(
        f"/api/resumes/{resume_id}/chat", headers={"X-User-Id": priya}
    ).json()["tokens_left"]

    _accept(client, priya, sid)

    after = client.get(
        f"/api/resumes/{resume_id}/chat", headers={"X-User-Id": priya}
    ).json()["tokens_left"]
    assert after == before, (
        f"an accepted suggestion cost {before - after} chat token(s); events "
        "are not model calls and must bypass the allowance"
    )


def test_the_event_is_served_on_the_history_endpoint_with_its_meta(client, priya):
    """A row the client cannot read is not a sync."""
    resume_id, sid = _suggestion(
        client, priya, "Rebuilt the deployment pipeline, cutting failures by 35 percent."
    )
    _accept(client, priya, sid)

    body = client.get(
        f"/api/resumes/{resume_id}/chat", headers={"X-User-Id": priya}
    ).json()
    events = [m for m in body["messages"] if m["role"] == "event"]
    assert len(events) == 1
    meta = events[0]["meta"]
    assert meta["kind"] == "suggestion_accepted"
    assert meta["target_ref"] == "exp_0.bullet_0"
    assert {"score_before", "score_after", "score_delta"} <= set(meta)


# ---------------------------------------------------------------- property 3


def test_a_real_improvement_carries_the_score_move(client, priya):
    improved = "Rebuilt the deployment pipeline, cutting release failures by 35 percent."
    resume_id, sid = _suggestion(client, priya, improved)
    _accept(client, priya, sid)

    meta = _events(client, resume_id)[0].meta
    expected_before = score_resume(SAMPLE)["overall_score"]
    patched = {
        **SAMPLE,
        "experience": [
            {
                **SAMPLE["experience"][0],
                "bullets": [improved, SAMPLE["experience"][0]["bullets"][1]],
            }
        ],
    }
    expected_after = score_resume(patched)["overall_score"]

    assert meta["score_before"] == expected_before
    assert meta["score_after"] == expected_after
    assert meta["score_delta"] == expected_after - expected_before
    assert meta["score_delta"] > 0, (
        "a weak-verb bullet replaced by a quantified one must price above "
        "zero, or the card is decorative"
    )


def test_an_edit_that_does_not_move_the_score_reports_zero(client, priya):
    """`score_delta == 0` is what suppresses the card.

    A "+0 points" badge after every accept teaches the user to stop reading
    the badge, which costs them the ones that matter.
    """
    # Same weak opener, same absent metric -- cosmetic only.
    cosmetic = "Responsible for the deployment pipeline and its general upkeep"
    resume_id, sid = _suggestion(client, priya, cosmetic)
    _accept(client, priya, sid)

    assert _events(client, resume_id)[0].meta["score_delta"] == 0


def test_the_applied_text_is_snapshotted_onto_the_event(client, priya):
    """The quoted block renders from `meta`, not from a live suggestion read.

    A transcript is a log. If the quote were resolved through `suggestion_id`
    at render time, editing or re-accepting that row later would retroactively
    rewrite history -- the chat would show text that was never applied at the
    moment the entry claims it was.
    """
    improved = "Rebuilt the deployment pipeline, cutting release failures by 35 percent."
    resume_id, sid = _suggestion(client, priya, improved)
    _accept(client, priya, sid)

    meta = _events(client, resume_id)[0].meta
    assert meta["applied_text"] == improved
    assert meta["placement"] == "Engineer - Acme"

    # Mutate the suggestion out from under the transcript.
    db = client._session_factory()
    try:
        row = db.query(Suggestion).filter(Suggestion.id == _uuid.UUID(sid)).first()
        row.suggested_text = "something else entirely"
        row.edited_text = "something else entirely"
        db.commit()
    finally:
        db.close()

    body = client.get(
        f"/api/resumes/{resume_id}/chat", headers={"X-User-Id": priya}
    ).json()
    event = next(m for m in body["messages"] if m["role"] == "event")
    assert event["meta"]["applied_text"] == improved, (
        "the transcript followed a later edit to the suggestion row; the "
        "quoted text must be the snapshot taken when it was applied"
    )


# ---------------------------------------------------------------- wiring


def test_the_dependency_graph_actually_supplies_a_chat_repository():
    """`chats` is optional on the service so tests can omit it.

    That default is a hole: drop the argument in deps.py and every mirroring
    test above would still pass through the service layer while the running
    app silently stopped logging. This pins the real wiring.
    """
    import inspect

    from app.api.deps import suggestion_service
    from app.repositories.chat_repository import ChatRepository

    params = inspect.signature(suggestion_service).parameters
    assert "chats" in params, "deps.suggestion_service no longer requests a ChatRepository"
    src = inspect.getsource(suggestion_service)
    assert "chats" in src.split("return")[-1], (
        "deps.suggestion_service takes a ChatRepository but does not pass it on"
    )
    assert ChatRepository.__name__ in str(params["chats"].annotation)
