"""A summary that must GROW needs the resume's substance; one that must not
invent a number must not be handed a pile of them.

Two summary findings want opposite things, and a single rule for "summary
targets" gets one of them wrong:

  summary.too_short   "Built platforms for 6 years." -> 30-60 words. Cannot
                      be done from the target text alone. With tone-only
                      grounding, claude-sonnet-5 correctly DECLINED
                      (changed=False) and groq refused in words -- and the
                      harness scored both as failures for being right.

  summary.no_metric   expects a DECLINE. Its whole point is that the model
                      must not reach for a number. The BASE resume's
                      experience is full of real ones (22->9 minutes, 30
                      services, 35 percent), so handing it that block would
                      quietly change what the case tests -- it is the
                      regression test for the 422 bug.

So the switch is keyed on the FINDING, never on the target.
"""

from __future__ import annotations

import copy

import pytest

from app.ai import agents
from app.services import heuristics
from tests import writer_cases as W


def _grounding(summary_text: str, finding_id: str) -> str:
    data = copy.deepcopy(W.BASE)
    data.setdefault("summary", {})["text"] = summary_text
    _points, findings = heuristics._score_summary(data)
    finding = next(f.to_dict() for f in findings if f.id == finding_id)

    captured: dict = {}

    def spy(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("captured")

    agents.llm.is_configured = lambda: True
    agents.llm.structured = spy
    try:
        agents.rewrite_for_finding(data, "summary.text", finding)
    except Exception:  # noqa: BLE001
        pass
    assert captured, "the agent never called the model"
    return captured["system"]


@pytest.fixture(autouse=True)
def _restore_llm(monkeypatch):
    """_grounding monkeypatches module attributes directly; undo them."""
    import app.ai.llm as real

    monkeypatch.setattr(agents, "llm", real, raising=True)
    yield


def test_too_short_is_given_real_material_to_expand_from():
    system = _grounding("Built platforms for 6 years.", "summary.too_short")
    for fact in (
        "Senior Platform Engineer",
        "Reduced CI pipeline runtime from 22 minutes to 9 minutes.",
        "Led the on-call rotation",
    ):
        assert fact in system, (
            f"{fact!r} missing -- the model cannot reach 30-60 words without "
            "it, and will correctly decline instead"
        )


def test_no_metric_is_NOT_given_the_numbers_it_must_not_borrow():
    case = next(c for c in W.CASES if c["id"] == "summary_no_metric")
    system = _grounding(case["patch"]["summary.text"], "summary.no_metric")
    for number in ("22 minutes", "35 percent", "40 services", "8 engineering"):
        assert number not in system, (
            f"{number!r} leaked into the no_metric prompt. That case is the "
            "regression test for the 422 bug and expects a DECLINE; feeding "
            "it real metrics changes what it measures."
        )


def test_the_two_paths_actually_differ():
    """Guard the guard -- if both returned the same block, the tests above
    would still pass individually while the distinction was gone."""
    short = _grounding("Built platforms for 6 years.", "summary.too_short")
    case = next(c for c in W.CASES if c["id"] == "summary_no_metric")
    bare = _grounding(case["patch"]["summary.text"], "summary.no_metric")
    assert short != bare


def test_the_switch_is_keyed_on_the_finding_not_the_target():
    """Both are summary.text; only the finding id separates them."""
    import inspect

    src = inspect.getsource(agents.rewrite_for_finding)
    assert 'finding.get("id")' in src
    assert "summary.too_short" in src


def test_composer_states_a_length_floor():
    """Run 2 had nemotron compose summaries that instantly tripped
    summary.too_short -- 'introduced summary.too_short' on a COMPOSE case.
    An upper bound with no floor lets a model satisfy the prompt and fail
    the rule."""
    from app.ai import prompts

    assert "30 and 60 words" in prompts.COMPOSER
    assert "Under 40 words" not in prompts.COMPOSER
