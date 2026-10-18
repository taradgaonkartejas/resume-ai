"""A writer that declines everything must FAIL, not pass.

Before this floor existed, `writer_report --llm` graded only safety:
INCONCLUSIVE if the chain collapsed, FAIL on an escaped fabrication, WARN on
a contained one, otherwise exit 0. So a writer that changed nothing at all
scored

    fix rate           0/7
    resisted inventing 3/3
    FABRICATIONS       0   (must be 0)

and exited 0. Silence passed every safety metric perfectly while being
useless -- the identical vacuity hole already found in ats_report.py and
closed there with MIN_CORRECT_RATE.

The measured baseline this protects is 7/7 fix, 7/7 reaching the user, 0
fabrications, on codecraft:claude-sonnet-5 in a clean single-model run.
"""

from __future__ import annotations

import re
import sys
from unittest import mock

import pytest

import app.ai.llm as llm_mod
from app.ai import agents
from app.ai.schemas import RevisionDraft
from scripts import writer_report


def _run(make_draft, argv):
    """Run the report with a stubbed writer. Writer and critic are given
    DIFFERENT model ids so the independence check cannot fire -- otherwise
    the run exits 2 for the wrong reason, which is what the first version of
    this test actually did."""
    def structured(**kwargs):
        return make_draft(kwargs), llm_mod.CallResult(
            content=None, model="sim:writer", latency_ms=5
        )

    def chain(task):
        return ["sim:critic"] if task == "critique" else ["sim:writer"]

    with mock.patch.object(agents.llm, "structured", structured), \
         mock.patch.object(agents.llm, "is_configured", lambda: True), \
         mock.patch.object(llm_mod, "resolve_chain", chain), \
         mock.patch.object(writer_report.llm, "resolve_chain", chain), \
         mock.patch.object(sys, "argv", argv):
        return writer_report.main()


def _unchanged(kwargs):
    found = re.search(r"Current text:\n(.*)", kwargs.get("user", ""), re.S)
    return RevisionDraft(
        suggested_text=found.group(1).strip() if found else "unchanged",
        keywords=[],
        reasoning="no change",
    )


def test_a_writer_that_declines_everything_fails(capsys):
    code = _run(_unchanged, ["writer_report", "--llm"])
    out = capsys.readouterr().out
    assert "FABRICATIONS       0" in out, "safety metrics should look perfect"
    assert "resisted inventing 3/3" in out
    assert code == 2, "silence must not pass"
    assert "below the floor" in out


def test_it_fails_on_the_floor_not_on_independence(capsys):
    """Guard the guard. The first attempt at this test exited 2 via the
    INCONCLUSIVE branch because the stub put writer and critic on one model,
    which would have 'passed' while testing nothing."""
    _run(_unchanged, ["writer_report", "--llm"])
    out = capsys.readouterr().out
    assert "INCONCLUSIVE" not in out
    assert "fix rate 0/7 is below the floor" in out


def test_the_offline_rule_writer_is_exempt(capsys):
    """`make writer-report` pins the rule writer at exactly 2/7 by design.

    It is a deterministic fallback, not a writer, so an LLM floor applied to
    it fails the build on correct behaviour -- the first version of this gate
    did exactly that, and running it is the only reason I found out.
    """
    with mock.patch.object(sys, "argv", ["writer_report"]):
        code = writer_report.main()
    out = capsys.readouterr().out
    assert "fix rate           2/7" in out, "the rule writer's pinned score moved"
    assert "below the floor" not in out, "the LLM floor leaked into offline mode"
    assert code == 0, "the offline report must stay green at 2/7"


@pytest.mark.parametrize("name", ["MIN_FIX_RATE", "MIN_REACHES_USER"])
def test_the_thresholds_are_expressible_on_seven_cases(name):
    """A threshold a metric cannot represent is not the gate you set.

    With 7 fixable cases only k/7 is reachable, so 0.70 means ">= 5/7" and
    not "70%". Pre-registering a value that falls between two reachable
    points is how a '10% max false-positive' bar silently became a zero-FP
    bar on 6 negatives earlier in this project.
    """
    value = getattr(writer_report, name)
    reachable = [k / 7 for k in range(8)]
    assert any(abs(value - r) < 0.02 or value < r for r in reachable)
    assert round(value * 7) == 5, f"{name} should mean 5 of 7"
