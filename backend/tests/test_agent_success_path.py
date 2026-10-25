"""A WORKING chat agent must produce the agent's answer, not the fallback.

Reported 2026-10-24 from the running app, server log:

```
Chat agent unavailable, using rule-based reply:
'ChatReply' object has no attribute 'reply'
```

The writer had answered correctly. `chat_service._agent_respond` then read
`reply.reply`, but the real `ChatReply` schema's field is `message` -- the
AttributeError was swallowed by the broad except, the user got the
rule-based reply under an "agent unreachable" banner, and a good model call
was billed and discarded.

Every existing test stopped short: they monkeypatched `agents.chat_reply`
to return None or to raise, so the line that reads `.reply`/`message` never
ran against the real schema until production. These tests build the REAL
`ChatReply` and `CriticVerdict` models and push them through the full
`_agent_respond` path with only the transport mocked, so any future drift
between `app.ai.schemas` field names and `chat_service` attribute reads
fails here first.
"""

from __future__ import annotations

import pytest

from app.ai import agents as agents_mod
from app.ai.schemas import ChatReply, CriticVerdict
from tests.conftest import new_resume
from tests.test_chat_loop import SAMPLE, _configured, _send


@pytest.fixture
def resume_id(client, priya):
    return new_resume(client, priya, "AgentOK", dict(SAMPLE))


def _agent_ok(monkeypatch, reply: ChatReply):
    """A reachable writer returning a real ChatReply instance."""
    monkeypatch.setattr(
        agents_mod,
        "chat_reply",
        lambda *a, **k: agents_mod.AgentOutcome(
            value=reply,
            agent="chat",
            model="m",
            latency_ms=1,
            tokens_in=1,
            tokens_out=1,
        ),
    )


def _critic_approves(monkeypatch):
    monkeypatch.setattr(
        agents_mod,
        "review_draft",
        lambda *a, **k: agents_mod.AgentOutcome(
            value=CriticVerdict(approved=True, notes="", severity="none"),
            agent="critic",
            model="m",
            latency_ms=1,
            tokens_in=1,
            tokens_out=1,
        ),
    )


def test_a_reply_without_a_change_reaches_the_user(
    client, priya, resume_id, monkeypatch
):
    _configured(monkeypatch)
    _agent_ok(
        monkeypatch,
        ChatReply(message="Your experience section is strong already."),
    )

    body = _send(client, priya, resume_id, "How does my experience look?")

    assert body["message"]["content"] == "Your experience section is strong already."
    assert body["suggestion"] is None
    # Crucially: NO degraded banner. The agent answered; pretending it
    # failed is the lie that filed this test.
    meta = body["message"].get("meta") or {}
    assert meta.get("kind") != "agent_unreachable"


def test_a_proposed_change_becomes_a_suggestion_when_the_critic_approves(
    client, priya, resume_id, monkeypatch
):
    _configured(monkeypatch)
    _agent_ok(
        monkeypatch,
        ChatReply(
            message="I tightened your pipeline bullet.",
            proposes_change=True,
            target_ref="exp_0.bullet_0",
            suggested_text="Owned the deployment pipeline and its upkeep.",
            reasoning="Leads with ownership.",
        ),
    )
    _critic_approves(monkeypatch)

    body = _send(client, priya, resume_id, "In experience, tighten the first bullet")

    assert body["message"]["content"] == "I tightened your pipeline bullet."
    sugg = body["suggestion"]
    assert sugg is not None, "approved draft was dropped"
    assert sugg["target_ref"] == "exp_0.bullet_0"
    assert sugg["section"] == "experience"
    assert sugg["suggested_text"].startswith("Owned")


def test_a_critic_rejection_still_shows_the_agent_reply(
    client, priya, resume_id, monkeypatch
):
    _configured(monkeypatch)
    _agent_ok(
        monkeypatch,
        ChatReply(
            message="I cannot invent a metric for that bullet.",
            proposes_change=True,
            target_ref="exp_0.bullet_0",
            suggested_text="Increased revenue by 500%.",
            reasoning="stronger numbers",
        ),
    )
    monkeypatch.setattr(
        agents_mod,
        "review_draft",
        lambda *a, **k: agents_mod.AgentOutcome(
            value=CriticVerdict(approved=False, notes="invented", severity="high"),
            agent="critic",
            model="m",
            latency_ms=1,
            tokens_in=1,
            tokens_out=1,
        ),
    )

    body = _send(client, priya, resume_id, "make it sound bigger")

    assert body["message"]["content"] == "I cannot invent a metric for that bullet."
    assert body["suggestion"] is None
    meta = body["message"].get("meta") or {}
    assert meta.get("kind") != "agent_unreachable"
