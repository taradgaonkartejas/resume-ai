"""The writer corpus: integrity of the cases, and the rule engine's envelope.

Split the same way as test_critic_corpus.py -- everything here runs offline
except the live-writer block at the bottom, which skips without a key.
"""

import pytest

from app.ai import agents, llm
from app.services import resume_ops
from app.services.heuristics import score_resume
from tests.writer_cases import BASE, CASES, resume_for, to_decline, to_fix

# --------------------------------------------------------- corpus integrity


def test_the_base_resume_is_perfect():
    """Every case measures its own patch, so the baseline must contribute none.

    If BASE ever develops a finding, every case silently starts measuring two
    things and the fix rate becomes uninterpretable.
    """
    scored = score_resume(BASE)
    assert scored["overall_score"] == 100
    assert scored["findings"] == [], [f["id"] for f in scored["findings"]]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_each_case_introduces_exactly_one_finding(case):
    ids = {f["id"] for f in score_resume(resume_for(case))["findings"]}
    assert ids == {case["code"]}, (
        f"{case['id']} introduced {sorted(ids)}; a case that trips two findings "
        "cannot tell you which one the writer fixed"
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_every_case_targets_a_resolvable_ref(case):
    # summary.missing resolves to "" by design -- that is the compose path.
    assert resume_ops.exists(resume_for(case), case["target_ref"])


def test_the_corpus_covers_both_honest_outcomes():
    """A corpus of only fixable cases cannot detect fabrication."""
    assert len(to_fix()) >= 5
    assert len(to_decline()) >= 3


def test_decline_cases_really_cannot_be_fixed_without_inventing():
    """The label has to be true, or the fabrication metric means nothing.

    For each must-decline case the target text contains no digit, so any digit
    in a rewrite is necessarily invented rather than moved.
    """
    import re

    for case in to_decline():
        text = resume_ops.resolve(resume_for(case), case["target_ref"])
        assert not re.search(r"\d", text), (
            f"{case['id']}: target already contains a number, so 'fixing' it "
            "would not require invention and the label is wrong"
        )


# ------------------------------------------------- the rule engine's envelope


def _rule_draft(case):
    data = resume_for(case)
    scored = score_resume(data)
    finding = next(f for f in scored["findings"] if f["id"] == case["code"])
    return data, agents._rule_rewrite(
        resume_ops.resolve(data, case["target_ref"]),
        finding,
        not resume_ops.resolve(data, case["target_ref"]).strip(),
        data,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_the_rule_writer_never_invents_a_number(case):
    """The floor under the whole feature, and it does not depend on a model.

    _rule_rewrite is what runs when the LLM is unreachable. If it can fabricate,
    then every outage becomes a correctness incident rather than a degradation.
    """
    import re

    data, draft = _rule_draft(case)
    known = set(re.findall(r"\d+", resume_ops.to_plain_text(data)))
    produced = set(re.findall(r"\d+", draft.get("suggested_text") or ""))
    assert not (produced - known), (
        f"{case['id']}: rule writer invented {sorted(produced - known)}"
    )


def test_rule_writer_scores_exactly_as_expected():
    """Headline numbers, so a regression is one line in the diff.

    3/7 is not good. It is a measurement, and it is the number the live writer
    has to beat to justify the API call.
    """
    fixed = 0
    for case in to_fix():
        data = resume_for(case)
        before = score_resume(data)
        finding = next(f for f in before["findings"] if f["id"] == case["code"])
        outcome = agents.rewrite_for_finding(data, case["target_ref"], finding)
        text = (outcome.value.get("suggested_text") or "").strip()
        if not text or text == resume_ops.resolve(data, case["target_ref"]).strip():
            continue
        after = score_resume(resume_ops.apply_patch(data, case["target_ref"], text))
        after_ids = {f["id"] for f in after["findings"]}
        if case["code"] not in after_ids and after_ids <= {
            f["id"] for f in before["findings"]
        }:
            fixed += 1

    assert fixed == 2, (
        f"rule writer fixed {fixed}/{len(to_fix())}; expected exactly 2. "
        "If you improved _rule_rewrite, update this number deliberately."
    )


# ------------------------------------------------------------- the live writer
_HAS_KEY = llm.is_configured()
def _skip(fn):
    """Skip without a key; opt back in to real keys when there is one."""
    fn = pytest.mark.skipif(_HAS_KEY is False, reason="no provider API key")(fn)
    return pytest.mark.live(fn)


@_skip
@pytest.mark.parametrize("case", to_decline(), ids=lambda c: c["id"])
def test_the_live_writer_does_not_invent_a_metric(case):
    """The safety property. Outranks the fix rate entirely."""
    import re

    data = resume_for(case)
    scored = score_resume(data)
    finding = next(f for f in scored["findings"] if f["id"] == case["code"])
    outcome = agents.rewrite_for_finding(data, case["target_ref"], finding)
    if outcome.status == "degraded":
        pytest.skip("writer unreachable; this measures transport, not the writer")
    known = set(re.findall(r"\d+", resume_ops.to_plain_text(data)))
    produced = set(re.findall(r"\d+", outcome.value.get("suggested_text") or ""))
    assert not (produced - known), f"invented {sorted(produced - known)}"


# --------------------------------------------------- the offline mode is offline
def test_rule_mode_makes_no_llm_call_even_with_a_key_configured(monkeypatch):
    """`mode: rule engine` must mean no network call, on any machine.

    This did not hold. _evaluate(case, use_llm) accepted the flag and ignored
    it, so `make writer-report` on a machine WITH an API key measured the LLM
    and labelled the result "rule engine". It looked correct everywhere a key
    was absent, which is why it survived: the bug was invisible in exactly
    the environment used to verify it.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import writer_report

    calls = []

    def _explode(*a, **k):
        calls.append(a)
        raise AssertionError("rule mode called the LLM")

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "structured", _explode)
    monkeypatch.setattr(llm, "complete", _explode)

    result = writer_report._evaluate(to_fix()[0], use_llm=False)

    assert not calls, "rule mode must not reach the LLM"
    assert result.get("model", "rule-engine") == "rule-engine"
