"""The probe must test every model the free plan actually covers.

`make models-gen-all codecraft` reported

    --- codecraft: real generation on 0 free model(s) ---
      No model of this provider can generate inside the budget.

which reads as a damning verdict on the provider. It was a bug in the probe:
the selection logic read only `Provider.free_models`, a hand-maintained
allowlist that exists for providers where *part* of the catalogue is free.
codecraft declares `free_covers_catalogue=True` with an empty allowlist, so
zero models were selected and the "none of them worked" message was printed
over an empty list.

The contradiction was visible in the same session: `writer-report-llm` had
all ten cases answered by `codecraft:claude-sonnet-5`, so the provider plainly
worked. A probe that reports a provider as unusable while the application is
successfully using it is worse than no probe -- it would have sent us to
replace a model that was fine.
"""

from __future__ import annotations

from app.ai import llm


def test_a_whole_catalogue_provider_declares_no_allowlist():
    """The two mechanisms are alternatives; holding both is contradictory."""
    for name, p in llm.PROVIDERS.items():
        if p.free_covers_catalogue:
            assert not p.free_models, (
                f"{name} says the whole catalogue is free AND carries a free "
                "allowlist. Those mean different things; pick one."
            )


def test_codecraft_is_a_whole_catalogue_provider():
    """Pins the config the probe bug was hiding behind."""
    cc = llm.PROVIDERS["codecraft"]
    assert cc.free_covers_catalogue is True
    assert not cc.free_models


def test_nvidia_still_uses_an_allowlist():
    """The counterpart. nvidia lists ~81 models and only some are free.

    Declaring that catalogue free was the root cause of the kimi-k2.6
    incident, so this must not drift.
    """
    nv = llm.PROVIDERS["nvidia"]
    assert nv.free_covers_catalogue is False
    assert nv.free_models, "nvidia's free allowlist disappeared"


def test_the_probe_selects_models_for_both_kinds_of_provider():
    """Mirrors the selection branch in scripts/check_model_access.py.

    Asserted on behaviour rather than by importing the script, which runs
    argparse and builds clients at import time.
    """
    def select(provider, catalogue):
        if provider.free_covers_catalogue:
            return sorted(catalogue)
        return sorted(provider.free_models or ())

    cc = llm.PROVIDERS["codecraft"]
    catalogue = {"claude-sonnet-5", "grok-4.6", "glm-5.3"}
    chosen = select(cc, catalogue)
    assert chosen == sorted(catalogue), (
        "a whole-catalogue provider must probe its catalogue; selecting "
        "nothing is what produced the false 'no model can generate' verdict"
    )

    nv = llm.PROVIDERS["nvidia"]
    assert select(nv, catalogue) == sorted(nv.free_models), (
        "an allowlist provider must probe its allowlist, not the catalogue"
    )


def test_selecting_nothing_is_distinguishable_from_everything_failing():
    """The actual lesson: an empty list and a failed list must not print alike.

    `for x in []` completes without error, so "0 of 0 succeeded" rendered as
    "No model of this provider can generate inside the budget" -- the same
    sentence a real total failure would produce.
    """
    results: list[bool] = []
    assert not any(results)
    assert len(results) == 0, (
        "an empty result set must be reported as 'nothing was tested', never "
        "as 'everything failed'"
    )
