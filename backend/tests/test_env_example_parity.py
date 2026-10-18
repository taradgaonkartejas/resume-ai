"""`.env.example` must not drift from `config.py`, and a retired model must
not be reachable by editing `.env`.

Both guards exist because the same failure happened twice, quietly.

**Drift.** `.env.example` advertised
`MODEL_WRITING=nvidia:nvidia/nemotron-3-ultra-550b-a55b` and
`MODEL_ATS=groq:openai/gpt-oss-120b` long after `config.py` had moved both to
codecraft, and it never gained `EMBEDDING_BACKEND` or `LLM_CACHE_ENABLED` at
all. Anyone seeding a `.env` from the example pinned two stale models by hand
and then wondered why their run did not match the reports.

**Retirement.** Four model ids were withdrawn after being measured -- one
times out, one 404s, one was retired by the vendor, one pings fast and then
times out. The reasoning for each was written down *in a comment*. A comment
is not a guard: `MODEL_JD_ANALYSIS=nvidia:z-ai/glm-5.3-flash` sat in a real
`.env` and silently put the timing-out model back on the JD-analysis path.

These tests exist so that hand-editing models in `.env` -- which is how the
project is meant to be used -- stays safe.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.ai import llm
from app.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"

#: Settings fields that name a model or pick a backend. These are the ones a
#: user is invited to change by hand, so these are the ones that must agree.
TRACKED_PREFIXES = ("MODEL_", "EMBEDDING_", "LLM_CACHE")


def _example_values() -> dict[str, str]:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    return dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", text, re.M))


def _tracked_fields() -> dict[str, object]:
    return {
        name.upper(): field.default
        for name, field in Settings.model_fields.items()
        if name.upper().startswith(TRACKED_PREFIXES)
    }


def _same(example: str, default: object) -> bool:
    """Compare as strings, but let `false` match `False`."""
    if isinstance(default, bool):
        return example.strip().lower() == str(default).lower()
    return example == str(default)


# ------------------------------------------------------------------ drift ---


def test_the_example_file_exists():
    assert ENV_EXAMPLE.is_file(), f"{ENV_EXAMPLE} is missing"


@pytest.mark.parametrize("key", sorted(_tracked_fields()))
def test_every_tracked_setting_is_present_in_the_example(key):
    """A setting absent from the example is one nobody knows they can set."""
    assert key in _example_values(), (
        f"{key} is a Settings field but is not in .env.example. "
        "EMBEDDING_BACKEND and LLM_CACHE_ENABLED were both missing."
    )


@pytest.mark.parametrize("key", sorted(_tracked_fields()))
def test_the_example_value_equals_the_config_default(key):
    example = _example_values().get(key, "<absent>")
    default = _tracked_fields()[key]
    assert _same(example, default), (
        f".env.example has {key}={example!r} but config.py defaults to "
        f"{default!r}. config.py holds the reasoning next to each choice, so "
        "it wins -- update .env.example."
    )


def test_the_example_does_not_invent_settings_that_do_not_exist():
    """A MODEL_* line with no matching field is silently ignored at runtime."""
    known = {name.upper() for name in Settings.model_fields}
    unknown = [
        key for key in _example_values()
        if key.startswith("MODEL_") and key not in known
    ]
    assert not unknown, f"no Settings field for: {unknown}"


# --------------------------------------------------------------- retired ---


def test_the_example_never_suggests_a_retired_model():
    offenders = {
        key: value for key, value in _example_values().items()
        if key.startswith("MODEL_") and llm.is_retired(value)
    }
    assert not offenders, offenders


def test_the_example_never_suggests_an_excluded_model():
    offenders = {
        key: value for key, value in _example_values().items()
        if key.startswith("MODEL_") and value and llm.is_excluded(value)
    }
    assert not offenders, offenders


@pytest.mark.parametrize("model", sorted(llm.RETIRED_MODELS))
def test_a_retired_model_is_dropped_from_the_chain(model):
    """The point of the guard: setting it in .env must not route to it."""
    assert llm.is_retired(model)
    kept = llm._drop_retired([model, "groq:openai/gpt-oss-120b"], "jd_analysis")
    assert model not in kept
    assert "groq:openai/gpt-oss-120b" in kept


@pytest.mark.parametrize("model", sorted(llm.RETIRED_MODELS))
def test_every_retired_model_records_why(model):
    """A ban without a reason becomes cargo cult the next time someone looks."""
    reason = llm.RETIRED_MODELS[model]
    assert len(reason) > 30, f"{model}: reason too thin to act on"


def test_the_retired_list_warns_once_per_model(caplog):
    llm._RETIRED_LOGGED.clear()
    model = "nvidia:z-ai/glm-5.3-flash"
    with caplog.at_level("WARNING"):
        llm._drop_retired([model], "jd_analysis")
        llm._drop_retired([model], "jd_analysis")
    hits = [r for r in caplog.records if model in r.getMessage()]
    assert len(hits) == 1, "a per-call warning would flood the log"
    assert "RETIRED" in hits[0].getMessage()
    llm._RETIRED_LOGGED.clear()


def test_retirement_and_exclusion_stay_separate_lists():
    """Different reasons, different lifetimes.

    Exclusion is data protection and is absolute. Retirement is measured
    behaviour on this account and can legitimately be reversed if a provider
    fixes the model. Merging them would let a privacy ban be lifted by a
    latency measurement.
    """
    for model in llm.RETIRED_MODELS:
        assert not llm.is_excluded(model), (
            f"{model} is in both lists; a privacy exclusion must not depend "
            "on a performance observation"
        )


def test_resolve_chain_actually_applies_the_retirement_filter(monkeypatch):
    """Wiring, not behaviour -- `_drop_retired` is unit-tested above.

    Asserting on the RESULT of resolve_chain would need provider keys and
    would pass vacuously in CI, where the chain filters down to empty and
    raises. This asserts the filter is invoked for every task instead.
    """
    import contextlib

    seen: list[str] = []
    real = llm._drop_retired

    def spy(chain, task):
        seen.append(task)
        return real(chain, task)

    monkeypatch.setattr(llm, "_drop_retired", spy)
    tasks = ("extraction", "jd_analysis", "scoring", "ats", "writing",
             "critique", "chat")
    for task in tasks:
        with contextlib.suppress(llm.LLMUnavailable):
            llm.resolve_chain(task)
    assert set(seen) == set(tasks), f"not filtered for: {set(tasks) - set(seen)}"
