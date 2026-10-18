"""The writer's length instruction must match the rule it is being judged by.

`summary.too_short` was the only corpus case two different providers could
not pass, and the reason was in the prompt, not the models:

    heuristics.py  ->  "Summary is short (4 words) -- aim for 30-60"
    prompts.REVISER ->  "5. Keep the candidate's voice. Under 30 words..."

Expand past 30 and stay under 30, in the same request. groq refused outright
("I cannot expand the summary to 30-60 words without introducing new
information"); claude-sonnet-5 obeyed the cap and left the finding standing.
Both were marked failures by the harness.

A summary and a bullet are scored against opposite length rules, so one
sentence cannot serve both targets.
"""

from __future__ import annotations

import pytest

from app.ai import prompts
from app.ai.agents import _length_rule


@pytest.mark.parametrize("ref", ["summary.text", "summary"])
def test_a_summary_is_told_to_expand(ref):
    rule = _length_rule(ref)
    assert "30" in rule and "60" in rule
    assert "under 30" not in rule.lower(), (
        "this is the exact contradiction that made summary.too_short unpassable"
    )


@pytest.mark.parametrize(
    "ref", ["exp_0.bullet_0", "exp_1.bullet_3", "experience.bullets"]
)
def test_a_bullet_keeps_its_cap(ref):
    rule = _length_rule(ref)
    assert "Under 30 words" in rule


def test_the_rule_satisfies_the_heuristic_it_is_judged_against():
    """>= 25 words clears `summary.too_short`; the instruction must exceed it.

    Pinned numerically rather than by wording, so that moving the threshold
    in heuristics.py breaks this test instead of silently re-opening the
    contradiction.
    """
    import re

    rule = _length_rule("summary.text")
    lows = [int(n) for n in re.findall(r"\d+", rule)]
    assert lows, "the summary rule states no number at all"
    assert min(lows) >= 25, (
        f"the summary length instruction ({min(lows)}) is below the 25-word "
        "threshold that clears summary.too_short, so following it would "
        "leave the finding in place"
    )


def test_the_reviser_prompt_no_longer_hardcodes_a_length():
    assert "{length_rule}" in prompts.REVISER
    assert "Under 30 words, starting with a strong verb." not in prompts.REVISER


def test_the_prompt_renders_differently_per_target():
    summary = prompts.REVISER.format(
        context="(none)", length_rule=_length_rule("summary.text")
    )
    bullet = prompts.REVISER.format(
        context="(none)", length_rule=_length_rule("exp_0.bullet_0")
    )
    assert summary != bullet
    assert "30 to 60" in summary
    assert "Under 30 words" in bullet


def test_the_batch_writer_prompt_is_bullets_only_so_its_cap_is_correct():
    """WRITER rewrites bullets exclusively, so its flat 30-word cap is right.

    Stated explicitly so nobody 'fixes' it by symmetry with REVISER.
    """
    assert "under 30 words" in prompts.WRITER.lower()
    assert "{length_rule}" not in prompts.WRITER
