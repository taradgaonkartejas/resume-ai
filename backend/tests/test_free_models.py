"""Free tier only.

These tests exist so a paid model cannot re-enter by accident. The failure mode
is silent and expensive: the fallback chain only runs when the preferred model
is unavailable, so a paid entry bills nobody's attention until the invoice.
"""

import re
from pathlib import Path

import pytest

from app.ai import llm
from app.ai.llm import (
    FALLBACK_CHAIN,
    PROVIDERS,
    TASK_MODELS,
    LLMUnavailable,
    is_free,
    resolve_chain,
)
from app.config import is_placeholder, settings


@pytest.fixture(autouse=True)
def _a_key_for_every_provider(monkeypatch):
    """resolve_chain() now drops models whose provider has no key.

    That is the point of the change, but it means these tests must declare a
    key or every chain resolves to empty for the wrong reason.
    """
    for provider in PROVIDERS.values():
        monkeypatch.setattr(settings, provider.key_setting, "test-key")


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


# --------------------------------------------------------- provider registry
def test_freeness_is_declared_per_provider_not_guessed_from_the_name():
    """The old rule was `endswith(":free")` -- UnoRouter's naming convention.

    No Groq or NVIDIA model is named that way, so the old rule would have
    refused every model on the new providers and resolved every chain to
    empty: every call would have raised before it was sent.
    """
    assert is_free("nvidia:nvidia/nemotron-3-ultra-550b-a55b")  # Free Endpoint
    assert is_free("groq:openai/gpt-oss-120b")          # on the free list
    assert not is_free("groq:some-paid-model")          # not on it
    assert is_free("unorouter:gemini-3.5-flash-lite:free")
    assert not is_free("unorouter:gemini-3-flash")


def test_an_unqualified_name_is_never_vouched_for():
    """Otherwise a bare paid name inherits the default provider's free tier."""
    assert not is_free("gemini-3-flash")
    assert not is_free("some-expensive-model")
    # ...but the legacy ":free" convention still resolves.
    assert is_free("gemini-3.5-flash-lite:free")


def test_a_legacy_unqualified_id_routes_back_to_unorouter():
    """Not to the new default provider, which has never heard of it.

    Misrouting here reappears as a 403 'this token has no access to model X',
    which reads like an entitlement problem and is really a routing bug.
    """
    assert llm.split_model("gemini-3.5-flash-lite:free") == (
        "unorouter",
        "gemini-3.5-flash-lite:free",
    )
    assert llm.split_model("groq:openai/gpt-oss-120b") == ("groq", "openai/gpt-oss-120b")


def test_the_critic_runs_on_a_different_vendor_from_the_writer():
    """The critic exists to catch what the writer got wrong.

    Same model for both makes that independence notional -- one network with
    one set of blind spots, grading its own homework.
    """
    writer = llm.split_model(TASK_MODELS["writing"])[0]
    critic = llm.split_model(TASK_MODELS["critique"])[0]
    assert writer != critic, f"writer and critic share provider {writer!r}"


def test_a_chain_spans_providers_so_a_429_has_somewhere_to_go():
    vendors = {llm.split_model(m)[0] for m in resolve_chain("critique")}
    assert len(vendors) > 1, f"critique chain sits on one vendor: {vendors}"


def test_models_for_unconfigured_providers_are_skipped(monkeypatch):
    """Holding one vendor's key should not mean a round trip to discover the rest."""
    for provider in PROVIDERS.values():
        monkeypatch.setattr(settings, provider.key_setting, "")
    monkeypatch.setattr(settings, "nvidia_api_key", "nv-test")

    chain = resolve_chain("critique")
    assert chain, "an nvidia-only setup must still resolve"
    assert all(llm.split_model(m)[0] == "nvidia" for m in chain), chain


def test_no_keys_at_all_is_not_configured(monkeypatch):
    for provider in PROVIDERS.values():
        monkeypatch.setattr(settings, provider.key_setting, "")
    assert llm.is_configured() is False


def test_one_key_is_enough_to_be_configured(monkeypatch):
    for provider in PROVIDERS.values():
        monkeypatch.setattr(settings, provider.key_setting, "")
    monkeypatch.setattr(settings, "groq_api_key", "gsk-test")
    assert llm.is_configured() is True


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
def test_only_tuned_tasks_set_reasoning_effort():
    from app.ai.llm import TASK_MODELS, effort_for

    tuned = {"critique": "minimal", "writing": "low"}
    assert effort_for("critique") == "minimal"
    assert effort_for("writing") == "low"
    for task in TASK_MODELS:
        if task not in tuned:
            assert effort_for(task) is None, f"{task} should use the provider default"


def test_writing_lowers_reasoning_effort():
    """Reversed deliberately, on measurement.

    This test previously asserted the opposite -- "writing is generative;
    only the binary judgement gets trimmed" -- and that was a reasonable
    prior. It did not survive contact with the numbers.

    Measured with per-case timing on the writer corpus, one model
    (nemotron-3-super-120b-a12b), same RAG context:

        summary rewrites   27.6 - 42.1s   against a 45s budget
        bullet  rewrites    2.9 - 10.1s

    At ~17 output tokens per visible word, most of that is reasoning, and two
    summary cases in an earlier run crossed 45s and timed out outright. The
    chain then fell through to another vendor mid-run, which also destroyed
    the writer/critic independence for those cases.

    Rewriting one sentence to fix one stated problem is not a reasoning task,
    which is the same argument already applied to the critic.

    This is a QUALITY TRADE and it is measured, not assumed: compare fix rate
    and the `sec` column across `make writer-report-llm` runs. If the fix
    rate falls, revert this line -- latency is worth less than correctness.
    """
    from app.ai.llm import effort_for

    assert effort_for("writing") == "low"


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
    """Not 'sends None' -- absent, so those payloads are byte-identical.

    Uses `extraction`, an untuned task. `writing` used to serve here and no
    longer can: it now sets "low" deliberately (see
    test_writing_lowers_reasoning_effort).
    """
    pytest.importorskip("langchain_openai")
    monkeypatch.setattr(settings, "unorouter_api_key", "test-key")
    from app.ai.llm import _client, effort_for

    params = _client("m", 0.2, effort_for("extraction"))._default_params
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


# ---------------------------------------------------------------------------
# Placeholder keys.
#
# Regression for a real incident: `.env.example` documented each key with a
# trailing comment on the assignment line --
#
#     CEREBRAS_API_KEY=     # cloud.cerebras.ai  1M tokens/day, 8k context cap
#
# Copied to `.env` and left unfilled, the 50-character comment was loaded as
# the key. `make models` then reported a 401 for a provider the user had
# deliberately not configured, which reads as an entitlement problem instead
# of the unset key it was. An unset key is free -- the provider is dropped
# with no network call. A garbage key costs a round trip and a misleading
# error, so it must be treated as unset.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "# cloud.cerebras.ai  1M tokens/day, 8k context cap",
        "# build.nvidia.com   ~40 req/min, no daily token cap",
        "<your-key>",
        "<YOUR_API_KEY_HERE>",
        "[paste key here]",
        "your-key-here",
        "your_api_key",
        "xxxxxxxx",
        "changeme",
        "TODO",
    ],
)
def test_placeholder_values_are_not_configured(value):
    assert is_placeholder(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "nvapi-abc123def456",
        "gsk_rUabcdefghijklmnop2Gxs",
        "sk-or-v1-0123456789",
        # A '#' *inside* a key is fine; only a leading one marks a comment.
        "abc#def",
    ],
)
def test_real_looking_keys_are_configured(value):
    assert is_placeholder(value) is False


def test_provider_with_comment_key_is_skipped_not_called(monkeypatch):
    """A commented-out key must drop the provider from the chain entirely."""
    provider = llm.PROVIDERS["nvidia"]
    monkeypatch.setattr(
        settings, provider.key_setting, "# build.nvidia.com   ~40 req/min"
    )
    assert provider.configured is False


def test_env_example_has_no_inline_comment_assignments():
    """The bug was in the template, so the template is what must stay fixed."""
    path = Path(__file__).resolve().parents[1] / ".env.example"
    offenders = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if re.match(r"^[A-Z][A-Z0-9_]*=", line) and "#" in line
    ]
    assert offenders == [], (
        "a trailing comment is loaded as part of the value; "
        f"move it above the line: {offenders}"
    )


def test_cerebras_is_gone():
    assert "cerebras" not in llm.PROVIDERS
    assert not hasattr(settings, "cerebras_api_key")


def test_unorouter_retained_for_free_suffix_backcompat():
    """Kept deliberately: an unqualified ':free' id must not reach NVIDIA."""
    assert "unorouter" in llm.PROVIDERS
    assert llm.split_model("gemini-3.5-flash-lite:free")[0] == "unorouter"


def test_glm_flash_not_in_shared_fallback_chain():
    """It burns the full timeout instead of answering (measured via `make models`).

    A timing-out model in the SHARED chain is not one task's problem: Groq's
    token cap means falling through is routine, so every task that reached it
    paid 45 seconds before moving on.
    """
    assert "nvidia:z-ai/glm-5.3-flash" not in llm.FALLBACK_CHAIN


def test_fallback_chain_spans_more_than_one_vendor():
    """The chain's whole purpose is surviving one vendor's rate limit.

    Caught for real: removing the timing-out glm-5.3-flash also removed the
    only NVIDIA entry, leaving an all-Groq chain in which a single 8k
    tokens/min cap would take out every tier simultaneously.
    """
    vendors = {llm.split_model(m)[0] for m in llm.FALLBACK_CHAIN}
    assert len(vendors) > 1, f"fallback chain is single-vendor: {vendors}"


# ---------------------------------------------------------------------------
# NVIDIA's free tier is a SUBSET of its catalogue.
#
# This registry used to declare free_covers_catalogue=True for NVIDIA, which
# is false. build.nvidia.com lists ~81 models; only those carrying a "Free
# Endpoint" badge are callable without credits. The rest return
# 404 "Function '<uuid>': Not found for account '<id>'" -- an error that reads
# like a broken model id rather than a billing boundary.
#
# That wrong flag is the root cause of the kimi-k2.6 incident. FREE_MODELS_ONLY
# is on by DEFAULT and exists precisely to refuse a non-free model before a
# request leaves the process. It waved kimi through, the call 404'd on all ten
# corpus cases, the chain fell through to the critic's own model, and a report
# was published under a model that never ran.
# ---------------------------------------------------------------------------


def test_nvidia_free_tier_does_not_cover_the_whole_catalogue():
    assert PROVIDERS["nvidia"].free_covers_catalogue is False
    assert PROVIDERS["nvidia"].free_models, "the badged allowlist must be populated"


@pytest.mark.parametrize(
    "model",
    [
        "nvidia:moonshotai/kimi-k2.6",   # 404 "Not found for account"
        "nvidia:z-ai/glm-5.3-flash",     # times out, no badge
    ],
)
def test_the_models_that_actually_failed_are_now_refused(model):
    """Regression: these two cost a full report run before being caught."""
    assert not is_free(model)


@pytest.mark.parametrize(
    "model",
    [
        "nvidia:nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia:nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia:nvidia/nemotron-3.5-lightning-30b-a3b",
    ],
)
def test_measured_free_endpoints_are_allowed(model):
    """Every id here answered a real 1-token call via `make models-probe`."""
    assert is_free(model)


@pytest.mark.parametrize(
    "model",
    [
        # Carries a "Free Endpoint" badge on build.nvidia.com and still times
        # out on every call. Badged is not the same as callable.
        "nvidia:google/gemma-4-31b-it",
        # Badged, and errors.
        "nvidia:nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        # Never existed -- a misreading of "ising-calibration-1.5-31b".
        "nvidia:nvidia/ising-calibration-1-35b-a3b",
    ],
)
def test_badged_but_unusable_models_are_refused(model):
    """The allowlist is built from measurement, not from reading the badges.

    Of twelve ids transcribed by eye from the Free Endpoint filter, three were
    wrong and five callable models were missed. `make models-probe` settled
    it with 65 real calls. Badged, listed and callable are three different
    properties and only the last one can be relied on.
    """
    assert not is_free(model)


def test_free_models_only_drops_unbadged_models_from_a_chain(monkeypatch):
    """The guard must bite before the network, not after a 404."""
    monkeypatch.setattr(settings, "nvidia_api_key", "nv-test")
    monkeypatch.setattr(settings, "groq_api_key", "gsk-test")
    monkeypatch.setattr(settings, "free_models_only", True)
    monkeypatch.setattr(settings, "model_writing", "nvidia:moonshotai/kimi-k2.6")

    chain = resolve_chain("writing")
    assert "nvidia:moonshotai/kimi-k2.6" not in chain, (
        "an unbadged model must never reach the transport"
    )


def test_every_configured_default_model_is_free():
    """No shipped default may be a model this plan cannot call."""
    for task in ("writing", "chat", "critique", "extraction", "jd_analysis", "scoring"):
        model = llm.TASK_MODELS[task]
        assert is_free(model), f"default for {task} is not a free endpoint: {model}"


def test_every_fallback_chain_entry_is_free():
    for model in llm.FALLBACK_CHAIN:
        assert is_free(model), f"fallback entry is not a free endpoint: {model}"
