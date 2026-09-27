"""Free tier only.

These tests exist so a paid model cannot re-enter by accident. The failure mode
is silent and expensive: the fallback chain only runs when the preferred model
is unavailable, so a paid entry bills nobody's attention until the invoice.
"""

import pytest

from app.ai import llm
from app.ai.llm import (
    FALLBACK_CHAIN,
    TASK_MODELS,
    LLMUnavailable,
    is_free,
    resolve_chain,
)
from app.config import settings


def test_the_default_model_is_free():
    assert is_free(settings.model_default), settings.model_default


def test_the_embedding_model_is_free():
    assert is_free(settings.embedding_model), settings.embedding_model


def test_free_models_only_defaults_on():
    assert settings.free_models_only is True


def test_every_model_in_the_fallback_chain_is_free():
    paid = [m for m in FALLBACK_CHAIN if not is_free(m)]
    assert not paid, f"paid models in FALLBACK_CHAIN: {paid}"


def test_every_task_model_is_free():
    paid = {t: m for t, m in TASK_MODELS.items() if not is_free(m)}
    assert not paid, f"paid models in TASK_MODELS: {paid}"


@pytest.mark.parametrize("task", sorted(TASK_MODELS))
def test_resolved_chain_is_free_for_every_task(task):
    chain = resolve_chain(task)
    assert chain, f"{task} resolved to an empty chain"
    assert all(is_free(m) for m in chain), chain


def test_a_paid_model_is_stripped_from_the_chain(monkeypatch):
    """Even if someone appends one later, it never reaches the network."""
    monkeypatch.setattr(llm, "FALLBACK_CHAIN", [settings.model_default, "gemini-3-flash"])
    chain = resolve_chain("chat")
    assert "gemini-3-flash" not in chain
    assert all(is_free(m) for m in chain)


def test_a_paid_default_is_refused_rather_than_billed(monkeypatch):
    """A paid MODEL_DEFAULT with no free fallback must fail loudly."""
    monkeypatch.setattr(llm, "FALLBACK_CHAIN", ["gemini-3-flash"])
    monkeypatch.setattr(llm, "TASK_MODELS", {"chat": "gemini-3-flash"})
    with pytest.raises(LLMUnavailable) as err:
        resolve_chain("chat")
    assert "free" in str(err.value).lower()


def test_opting_out_is_possible_but_explicit(monkeypatch):
    monkeypatch.setattr(settings, "free_models_only", False)
    monkeypatch.setattr(llm, "FALLBACK_CHAIN", [settings.model_default, "gemini-3-flash"])
    assert "gemini-3-flash" in resolve_chain("chat")


def test_is_free_recognises_the_suffix():
    assert is_free("gemini-3.5-flash-lite:free")
    assert is_free("  k2-horizon:free  ")
    assert not is_free("gemini-3-flash")
    assert not is_free("something:freemium")


# ------------------------------------------------------------- embeddings
def test_embeddings_are_local_by_default():
    assert settings.embeddings_remote is False


def test_local_embedding_matches_the_pgvector_column_width():
    """A vector that is not embedding_dim long cannot be stored."""
    from app.ai.embeddings import local_embed

    assert len(local_embed("kubernetes platform engineer")) == settings.embedding_dim


def test_embed_does_not_call_the_network_by_default(monkeypatch):
    from app.ai import embeddings

    def boom(_texts):
        raise AssertionError("remote embedder must not be called by default")

    monkeypatch.setattr(embeddings.llm, "embed_remote", boom)
    monkeypatch.setattr(embeddings.llm, "is_configured", lambda: True)
    assert embeddings.embed("kubernetes") == embeddings.local_embed("kubernetes")
    assert embeddings.embed_many(["a", "b"]) == [
        embeddings.local_embed("a"),
        embeddings.local_embed("b"),
    ]


def test_embeddings_stay_in_one_vector_space(monkeypatch):
    """Consistency is the point: same text in, same vector out, every time."""
    from app.ai import embeddings

    monkeypatch.setattr(embeddings.llm, "is_configured", lambda: True)
    assert embeddings.embed("platform engineer") == embeddings.embed("platform engineer")


# --------------------------------------------------- per-task reasoning effort
def test_only_the_critic_lowers_reasoning_effort():
    from app.ai.llm import TASK_MODELS, effort_for

    assert effort_for("critique") == "minimal"
    for task in TASK_MODELS:
        if task != "critique":
            assert effort_for(task) is None, f"{task} should use the provider default"


def test_writing_keeps_full_reasoning():
    """Writing is generative; only the binary judgement gets trimmed."""
    from app.ai.llm import effort_for

    assert effort_for("writing") is None


def test_an_unknown_task_sends_no_effort():
    from app.ai.llm import effort_for

    assert effort_for("not-a-task") is None


def test_critique_actually_puts_minimal_in_the_request_body(monkeypatch):
    """The table is only useful if it reaches the wire."""
    pytest.importorskip("langchain_openai")
    monkeypatch.setattr(settings, "unorouter_api_key", "test-key")
    from app.ai.llm import _client, effort_for

    params = _client("m", 0.0, effort_for("critique"))._default_params
    assert params.get("reasoning_effort") == "minimal"


def test_other_tasks_omit_the_field_entirely(monkeypatch):
    """Not 'sends None' -- absent, so those payloads are byte-identical."""
    pytest.importorskip("langchain_openai")
    monkeypatch.setattr(settings, "unorouter_api_key", "test-key")
    from app.ai.llm import _client, effort_for

    params = _client("m", 0.2, effort_for("writing"))._default_params
    assert "reasoning_effort" not in params


def test_lowering_effort_cannot_weaken_the_hard_checks():
    """The fabrication gate that does not depend on the model at all.

    review_draft rejects an unresolvable target_ref and a mismatched
    original_text before any LLM is consulted, so reasoning effort cannot
    affect them. This is the floor under the change.
    """
    from app.ai import agents

    data = {
        "experience": [
            {"company": "Acme", "role": "Eng", "dates": "", "bullets": ["Led the migration."]}
        ]
    }

    bad_ref = agents.review_draft(
        {"target_ref": "exp_9.bullet_9", "original_text": "x", "suggested_text": "y"}, data
    )
    assert bad_ref.value.approved is False
    assert bad_ref.value.severity == "fabrication"

    stale = agents.review_draft(
        {
            "target_ref": "exp_0.bullet_0",
            "original_text": "Something the resume never said.",
            "suggested_text": "y",
        },
        data,
    )
    assert stale.value.approved is False
    assert stale.value.severity == "fabrication"
