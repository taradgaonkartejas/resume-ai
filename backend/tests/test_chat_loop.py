"""The chat assistant must not ask the same question forever.

Reported from the running app: four messages, four identical replies, one of
which answered the question that had just been asked.

```
you   Suggest skills I am missing          <- a chip THIS BACKEND offers
ai    Could you be more specific about which section to improve?
you   Skills section                       <- the answer
ai    Could you be more specific about which section to improve?
```

That sentence is a hard-coded terminal string in the rule-based responder,
so every one of those turns fell through the whole agent path. Four separate
defects stacked to produce it, and each gets a test here:

  A. The agent was handed the current message and nothing else, so it could
     not see that it had just asked a clarifying question. Every turn was
     turn one, which makes a clarifying question an infinite loop by
     construction rather than merely unhelpful.

  B. "Suggest skills I am missing" is one of four quick actions the backend
     advertises, and the rule-based router matched on summary/bullet/
     quantify/impact only. The app offered a button it could not answer.

  C. Skills were invisible to the agent -- the addressable-refs block comes
     from `iter_bullets`, which yields summary and experience bullets only --
     even though `skills.N.items` is a patchable ref.

  D. Every failure (timeout, 429, dead model, bad key) was swallowed into an
     indistinguishable `None` and presented as a vague question, so an
     outage looked like stupidity.
"""

from __future__ import annotations

import uuid as _uuid

import pytest

from app.ai import agents as agents_mod
from app.models import ChatMessage, JobDescription, TailoringSession
from app.services import chat_service as chat_mod
from tests.conftest import new_resume

GENERIC = "Could you be more specific"

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


@pytest.fixture
def resume_id(client, priya):
    return new_resume(client, priya, "Loop", dict(SAMPLE))


def _send(client, user_id, resume_id, text):
    r = client.post(
        f"/api/resumes/{resume_id}/chat",
        json={"content": text},
        headers={"X-User-Id": user_id},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def _configured(monkeypatch, value=True):
    """Pretend a key is present without making a network call."""
    monkeypatch.setattr(
        type(chat_mod.settings), "llm_configured", property(lambda self: value)
    )


# ------------------------------------------------------------------ cause B


def test_every_advertised_quick_action_gets_a_real_answer(
    client, priya, resume_id, monkeypatch
):
    """A chip the backend ships must never produce the generic brush-off.

    Runs with a key CONFIGURED and the agent returning nothing, because that
    is the reported setup and it is the only one that can reach the generic
    terminal string. With no key the responder stops at the "running without
    an LLM key" message instead, and this test quietly proves nothing -- it
    passed against the un-fixed code until that was corrected.
    """
    _configured(monkeypatch)
    monkeypatch.setattr(
        agents_mod,
        "chat_reply",
        lambda *a, **k: agents_mod.AgentOutcome(value=None, agent="chat"),
    )

    actions = client.get(
        f"/api/resumes/{resume_id}/chat", headers={"X-User-Id": priya}
    ).json()["quick_actions"]
    assert actions, "backend advertised no quick actions"

    unanswered = []
    for action in actions:
        reply = _send(client, priya, resume_id, action)["message"]["content"]
        if GENERIC in reply:
            unanswered.append(action)

    assert not unanswered, (
        f"the backend offers these quick actions and cannot answer them: {unanswered}"
    )


@pytest.mark.parametrize(
    "message",
    [
        "Suggest skills I am missing",
        "I want to add skills that match given jd keyword",
        "skill an experince section",
        "Skills section",
    ],
)
def test_the_exact_messages_from_the_report_are_answered(
    client, priya, resume_id, message, monkeypatch
):
    # Same configuration as the report: a key is present, the agent yields
    # nothing, so the rule-based responder has to carry the answer.
    _configured(monkeypatch)
    monkeypatch.setattr(
        agents_mod,
        "chat_reply",
        lambda *a, **k: agents_mod.AgentOutcome(value=None, agent="chat"),
    )
    reply = _send(client, priya, resume_id, message)["message"]["content"]
    assert GENERIC not in reply, f"still falling through on: {message!r}"
    assert "skill" in reply.lower()


def test_skills_advice_never_writes_skills_into_the_resume(client, priya, resume_id):
    """Advisory by decision: an agent free to add skills invents them.

    This codebase has already shipped a fabricated "3+ years of GitOps"
    once. The reply may name gaps; it must not hand over a one-click accept
    that puts them on the resume.
    """
    body = _send(client, priya, resume_id, "Suggest skills I am missing")
    assert body["suggestion"] is None, (
        "the skills answer offered an acceptable edit; adding skills the "
        "candidate may not have is the one fabrication this must not make"
    )


def test_skills_advice_names_the_gap_when_a_job_description_exists(
    client, priya, resume_id
):
    db = client._session_factory()
    jd = JobDescription(title="SRE", content="kubernetes terraform")
    db.add(jd)
    db.flush()
    db.add(
        TailoringSession(
            resume_id=_uuid.UUID(str(resume_id)),
            job_description_id=jd.id,
            match_percent=10.0,
            baseline_percent=5.0,
            matched_keywords=["python"],
            gap_keywords=["kubernetes", "terraform"],
            thread_id="t",
            graph_state="done",
        )
    )
    db.commit()
    db.close()

    body = _send(client, priya, resume_id, "Suggest skills I am missing")

    # The gaps now ride on the card as selectable chips rather than being
    # listed in prose -- the user chose an applicable card over advice text.
    # The requirement is unchanged (the gaps must reach the user); only the
    # surface moved, so the assertion moves with it.
    card = body["suggestion"]
    assert card is not None, "no skills card was offered"
    assert {k.lower() for k in card["keywords"]} >= {"kubernetes", "terraform"}
    assert card["status"] == "needs_input"

    # It must still hand the judgement back rather than asserting the user
    # has these skills.
    reply = body["message"]["content"].lower()
    assert "genuinely" in reply or "only you" in reply


# ------------------------------------------------------------------ cause A


def test_the_agent_is_given_the_conversation_so_far(
    client, priya, resume_id, monkeypatch
):
    """Without history a clarifying question can never be answered."""
    seen: list[list[tuple[str, str]]] = []

    def fake_chat_reply(prompt, data, history=None, **kw):
        seen.append(list(history or []))
        return agents_mod.AgentOutcome(value=None, agent="chat")

    _configured(monkeypatch)
    monkeypatch.setattr(agents_mod, "chat_reply", fake_chat_reply)

    _send(client, priya, resume_id, "Make my summary more impactful")
    _send(client, priya, resume_id, "Skills section")

    assert len(seen) == 2
    assert seen[0] == [], "first message should have no prior history"
    assert seen[1], "second message reached the agent with NO history -- this is the loop"

    roles = [r for r, _ in seen[1]]
    texts = [t for _, t in seen[1]]
    assert "user" in roles and "assistant" in roles, (
        f"history must carry both sides of the exchange, got roles {roles}"
    )
    assert any("Make my summary more impactful" in t for t in texts)


def test_history_excludes_mirrored_events(client, priya, resume_id, monkeypatch):
    """Events are a log of pane activity, not things anyone said.

    Replaying "Applied to summary.text." as dialogue would invite the model
    to narrate the user's own edits back at them -- the authorship claim the
    chat pane exists to avoid.
    """
    seen: list[list[tuple[str, str]]] = []

    def fake_chat_reply(prompt, data, history=None, **kw):
        seen.append(list(history or []))
        return agents_mod.AgentOutcome(value=None, agent="chat")

    _configured(monkeypatch)
    monkeypatch.setattr(agents_mod, "chat_reply", fake_chat_reply)

    _send(client, priya, resume_id, "Make my summary more impactful")

    db = client._session_factory()
    db.add(
        ChatMessage(
            resume_id=_uuid.UUID(str(resume_id)),
            role="event",
            content="Applied to summary.text.",
            meta={"kind": "suggestion_accepted"},
        )
    )
    db.commit()
    db.close()

    _send(client, priya, resume_id, "and now?")

    assert not any("Applied to summary.text." in t for _, t in seen[-1]), (
        "a mirrored event leaked into the model's conversation history"
    )


def test_the_current_message_is_not_duplicated_into_its_own_history(
    client, priya, resume_id, monkeypatch
):
    seen: list[list[tuple[str, str]]] = []

    def fake_chat_reply(prompt, data, history=None, **kw):
        seen.append(list(history or []))
        return agents_mod.AgentOutcome(value=None, agent="chat")

    _configured(monkeypatch)
    monkeypatch.setattr(agents_mod, "chat_reply", fake_chat_reply)

    _send(client, priya, resume_id, "first")
    _send(client, priya, resume_id, "second")

    assert not any(t == "second" for _, t in seen[1]), (
        "history ended with the message being answered; it must stop at the "
        "previous exchange"
    )


# ------------------------------------------------------------------ cause C


def test_skills_reach_the_prompt_as_context_but_not_as_an_editable_ref():
    from app.ai import agents

    block = agents._skills_block(SAMPLE)
    assert "Python" in block and "SQL" in block

    refs = [ref for ref, _, _ in agents.resume_ops.iter_bullets(SAMPLE)]
    assert not any(r.startswith("skills") for r in refs), (
        "skills became directly addressable; the decision was advisory-only"
    )


def test_a_proposed_skills_edit_is_downgraded_to_advice(monkeypatch):
    """`skills.N.items` IS patchable, so the grounding check waves it through."""
    from app.ai import agents

    class Reply:
        proposes_change = True
        target_ref = "skills.0.items"
        suggested_text = "Python, SQL, Kubernetes"
        reasoning = "the JD wants it"
        reply = "Added Kubernetes."

    class Call:
        model = "m"
        latency_ms = 1
        tokens_in = 1
        tokens_out = 1

    monkeypatch.setattr(agents.llm, "is_configured", lambda: True)
    monkeypatch.setattr(agents.llm, "structured", lambda **kw: (Reply(), Call()))

    out = agents.chat_reply("add kubernetes", SAMPLE)
    assert out.value.proposes_change is False, (
        "the agent was allowed to write a skill onto the resume"
    )


# ------------------------------------------------------------------ cause D


def test_an_unreachable_agent_says_so_instead_of_asking_a_vague_question(
    client, priya, resume_id, monkeypatch
):
    def boom(*a, **k):
        raise TimeoutError("model timed out")

    _configured(monkeypatch)
    monkeypatch.setattr(agents_mod, "chat_reply", boom)

    body = _send(client, priya, resume_id, "rewrite my longest bullet")
    assert body["message"]["meta"].get("kind") == "agent_unreachable", (
        "an outage was presented to the user as an ordinary reply"
    )
    assert body["message"]["meta"].get("retryable") is True


def test_a_failed_agent_reply_costs_no_chat_token(
    client, priya, resume_id, monkeypatch
):
    before = client.get(
        f"/api/resumes/{resume_id}/chat", headers={"X-User-Id": priya}
    ).json()["tokens_left"]

    def boom(*a, **k):
        raise TimeoutError("model timed out")

    _configured(monkeypatch)
    monkeypatch.setattr(agents_mod, "chat_reply", boom)

    body = _send(client, priya, resume_id, "rewrite my longest bullet")
    assert body["tokens_left"] == before, (
        f"a non-answer cost {before - body['tokens_left']} token(s) of 25"
    )


def test_a_working_rule_based_reply_still_costs_a_token(client, priya, resume_id):
    """Only failures are free. A rule-based answer is still an answer."""
    before = client.get(
        f"/api/resumes/{resume_id}/chat", headers={"X-User-Id": priya}
    ).json()["tokens_left"]
    body = _send(client, priya, resume_id, "Make my summary more impactful")
    assert body["tokens_left"] == before - 1
    assert body["message"]["meta"].get("kind") != "agent_unreachable"


def test_the_notice_carries_why_not_just_that(client, priya, resume_id, monkeypatch):
    """A rate limit, an expired key and a timeout need different responses.

    "Unreachable" alone sends the user to the server log to find out which.
    """
    from app.ai import llm

    def boom(*a, **k):
        raise llm.LLMUnavailable(
            "All models failed for task chat: groq:openai/gpt-oss-120b "
            "rate_limit_exceeded (429); groq:openai/gpt-oss-20b timeout"
        )

    _configured(monkeypatch)
    monkeypatch.setattr(agents_mod, "chat_reply", boom)

    meta = _send(client, priya, resume_id, "hello")["message"]["meta"]
    assert meta["kind"] == "agent_unreachable"
    assert "429" in meta["reason"] or "rate_limit" in meta["reason"]
    assert "LLMUnavailable" in meta["reason"]


def test_an_api_key_echoed_back_by_a_provider_is_not_shown_to_the_user(
    client, priya, resume_id, monkeypatch
):
    """Provider errors sometimes quote the request back."""

    def boom(*a, **k):
        raise RuntimeError(
            "401 unauthorized for key gsk_liveSECRETvalue12345 on groq"
        )

    _configured(monkeypatch)
    monkeypatch.setattr(agents_mod, "chat_reply", boom)

    reason = _send(client, priya, resume_id, "hello")["message"]["meta"]["reason"]
    assert "gsk_liveSECRETvalue12345" not in reason, "an API key leaked to the client"
    assert "<redacted>" in reason
    assert "401" in reason, "scrubbing removed the diagnostic along with the key"
