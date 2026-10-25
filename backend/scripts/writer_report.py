"""Score the WRITER against tests/writer_cases.py.

    python -m scripts.writer_report              # rule engine only, offline
    python -m scripts.writer_report --llm        # and the live writer
    python -m scripts.writer_report --llm --sleep 3

The critic has been measured since day one. The writer -- the half a user
actually reads -- never has. This closes that.

There is no reference rewrite to diff against, deliberately: storing one
"known-good" answer would measure similarity to an arbitrary string rather
than quality. Instead every case starts from a resume that scores 100/100,
introduces exactly one finding, and asks the writer to fix it. The heuristics
that produced the finding then judge whether it is gone. Four mechanical
questions, no human in the loop:

    fixed        the finding no longer appears
    faithful     no digit appears that was not already in the resume, and the
                 production critic approves the draft
    clean        no NEW finding was introduced
    changed      not a no-op

Two headline numbers, and the second outranks the first:

    fix rate          of the cases that CAN be fixed from existing facts
    fabrication rate  MUST be zero

A writer that fixes everything by inventing numbers scores 100% on fixes and
is worse than useless. Cases marked `decline` cannot be fixed without
inventing a fact; for those, refusing IS the correct answer.

Exit codes
    0  the writer is at least as good as the rule engine and fabricated nothing
    1  it fabricated, or scored worse than the rule engine
    2  INCONCLUSIVE -- every call degraded, so nothing was measured
"""

from __future__ import annotations

import argparse
import contextlib
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai import agents, llm  # noqa: E402
from app.services import resume_ops  # noqa: E402
from app.services.heuristics import score_resume  # noqa: E402
from tests.writer_cases import CASES, resume_for, to_decline, to_fix  # noqa: E402

_DIGITS = re.compile(r"\d+")


def _finding(scored: dict, code: str) -> dict | None:
    return next((f for f in scored["findings"] if f["id"] == code), None)


def _new_digits(original_resume: dict, text: str) -> set[str]:
    """Digits in the draft that appear nowhere in the source resume.

    Checked against the WHOLE resume, not just the edited field: moving a real
    number from one bullet into the summary is legitimate, inventing one is not.
    """
    known = set(_DIGITS.findall(resume_ops.to_plain_text(original_resume)))
    return set(_DIGITS.findall(text)) - known


@contextlib.contextmanager
def _rules_only():
    """Make the whole agent layer behave as if no key were configured.

    `mode: rule engine` has to mean NO network call. Patching is_configured
    is how production already expresses "no LLM available", so the rule path
    exercised here is the real one, not a reimplementation.
    """
    original = llm.is_configured
    llm.is_configured = lambda: False
    try:
        yield
    finally:
        llm.is_configured = original


def _evaluate(case: dict, use_llm: bool) -> dict:
    # use_llm was accepted and then ignored: every "rule engine" run on a
    # machine with an API key silently measured the LLM and printed the rule
    # engine's name. The baselines quoted from those runs were not baselines.
    if not use_llm:
        with _rules_only():
            return _evaluate_inner(case)
    return _evaluate_inner(case)


def _evaluate_inner(case: dict) -> dict:
    data = resume_for(case)
    before = score_resume(data)
    finding = _finding(before, case["code"])
    if finding is None:  # pragma: no cover - guarded by a test
        return {"error": f"case {case['id']} no longer produces {case['code']}"}

    outcome = agents.rewrite_for_finding(data, case["target_ref"], finding)
    draft = outcome.value
    suggested = (draft.get("suggested_text") or "").strip()
    original = (draft.get("original_text") or "").strip()

    if outcome.status == "degraded":
        return {"degraded": True, "note": "; ".join(outcome.notes or [])[:70]}

    result = {
        "degraded": False,
        # The model that actually ANSWERED, not the first entry in the chain.
        # When a preferred model 404s the chain silently moves on, so printing
        # the intended model reports a run that did not happen.
        "model": getattr(outcome, "model", "?"),
        "latency_ms": int(getattr(outcome, "latency_ms", 0) or 0),
        "changed": bool(suggested) and suggested != original,
        "invented": sorted(_new_digits(data, suggested)),
        "used_llm": bool(getattr(outcome, "used_llm", False)),
    }

    if not result["changed"]:
        result.update(fixed=False, clean=True, approved=None)
        return result

    after_data = resume_ops.apply_patch(data, case["target_ref"], suggested)
    after = score_resume(after_data)
    result["fixed"] = _finding(after, case["code"]) is None
    before_ids = {f["id"] for f in before["findings"]}
    result["introduced"] = sorted({f["id"] for f in after["findings"]} - before_ids)
    result["clean"] = not result["introduced"]
    result["delta"] = after["overall_score"] - before["overall_score"]

    # The production critic, not a second opinion invented for this script.
    critique = agents.review_draft(
        {
            "target_ref": case["target_ref"],
            "original_text": original,
            "suggested_text": suggested,
        },
        data,
    )
    result["approved"] = critique.value.approved
    # WHICH layer rejected matters. The deterministic numeric guard in
    # _hard_check runs before the model and costs nothing; the LLM critic is
    # probabilistic. If every rejection comes from the model, the cheap layer
    # is not earning its place -- and if the guard is catching fabrications,
    # that is the layer we want doing it.
    notes = " ".join(critique.notes or [])
    if "hard check" in notes:
        result["verdict_by"] = "grd"
    elif getattr(critique, "used_llm", False):
        result["verdict_by"] = "llm"
    else:
        result["verdict_by"] = "rul"
    return result


# ---------------------------------------------------------------- thresholds
#
# Pre-registered, with the granularity written down -- because a threshold a
# metric cannot express is a different gate than the one you think you set.
# There are 8 fixable cases (one projects case joined on 2026-10-25 when
# projects became a scored category; its sibling decline case retired the
# same day when the owner dropped the projects metric check), so the only
# reachable rates are k/8:
#
#   0%  12.5%  25%  37.5%  50%  62.5%  75%  87.5%  100%
#
# 0.70 therefore means "at least 6 of 8". It is NOT a 70% bar.
#
# Why a floor is needed at all: before this, a writer that declined every
# single case scored `resisted inventing 3/3`, `FABRICATIONS 0` and exited 0.
# Silence passed a zero-fabrication bar trivially -- the identical vacuity
# hole already found and closed in ats_report.py with MIN_CORRECT_RATE. A
# test that cannot fail is worse than no test.
#
# The floor catches regression, it does not demand perfection: one flaky
# case must not turn a working writer red. Measured points for calibration:
#   codecraft:claude-sonnet-5          7/7   (2026-10-11, clean single-model)
#   codecraft:claude-sonnet-5          6/7   (earlier, length bug present)
#   nvidia:nemotron-3-ultra-550b       4/7   (fallback, visibly weaker)
# Those reference rows predate the two projects cases; denominators then were 7.
MIN_FIX_RATE = 0.70          # >= 6/8 in the 8-case corpus (was 5/7)
MIN_REACHES_USER = 0.70      # >= 6/8; a fix the critic blocks helps nobody


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.0)
    args = parser.parse_args()

    if args.llm and not llm.is_configured():
        print("No provider API key is set -- cannot score the live writer.")
        return 2

    mode = "live writer" if args.llm else "rule engine"
    print(f"\nwriter corpus: {len(CASES)} cases "
          f"({len(to_fix())} fixable, {len(to_decline())} must-decline)")
    print(f"mode: {mode}")
    if args.llm:
        chain = llm.resolve_chain("writing")
        print(f"chain: {' -> '.join(chain)}")
        print("       (the model that ANSWERED is shown per case; a preferred "
              "model that 404s is skipped silently)")

    width = max(len(c["id"]) for c in CASES) + 2
    print(f"\n{'case':<{width}}{'expect':<10}{'changed':<9}{'fixed':<7}"
          f"{'clean':<7}{'critic':<8}{'invented':<10}{'sec':<7}{'answered by'}")
    print("-" * (width + 79))

    statuses: Counter[str] = Counter()
    answered: set[str] = set()
    per_model: Counter[str] = Counter()
    contained = 0   # writer invented a number, critic rejected the draft
    escaped = 0     # writer invented a number, critic let it through
    fixed_ok = fabricated = 0
    declined_ok = 0
    shipped_ok = 0
    independence_lost = False
    reworded: list[str] = []

    for i, case in enumerate(CASES):
        if args.llm and args.sleep and i:
            time.sleep(args.sleep)
        r = _evaluate(case, args.llm)
        if r.get("error"):
            print(f"  {case['id']:<{width}} ERROR {r['error']}")
            statuses["error"] += 1
            continue
        if r["degraded"]:
            statuses["degraded"] += 1
            print(f"  {case['id']:<{width}}{case['expect']:<10}"
                  f"DEGRADED — {r['note']}")
            continue

        statuses["ok"] += 1
        invented = ",".join(r["invented"]) if r["invented"] else "-"
        if r["invented"]:
            fabricated += 1
            # A fabricated number that the critic REJECTED never reaches the
            # resume: the writer failed and the architecture caught it. One
            # the critic APPROVED is in the user's document. Both are writer
            # defects, only the second is a product defect, and collapsing
            # them hides whether the safety net is working.
            if r["approved"] is False:
                contained += 1
            else:
                escaped += 1
        crit = {True: "approve", False: "REJECT", None: "-"}[r["approved"]]
        if r["approved"] is False:
            crit = f"REJ:{r.get('verdict_by', '?')}"

        if case["expect"] == "fix":
            good = r.get("fixed") and r["clean"] and not r["invented"]
            fixed_ok += bool(good)
            # A draft the critic rejects never leaves the building: /rewrite
            # answers 422 and the user sees nothing. Counting it as a fix
            # measures the writer in isolation and overstates what the SYSTEM
            # does -- a 6/7 where two were rejected is a 4/7 to the person
            # using it.
            if good and r["approved"] is not False:
                shipped_ok += 1
            mark = "" if good else " X"
        else:
            # Declining means: do not invent. Leaving it unfixed is correct;
            # a faithful rewording that happens to fix it is also fine.
            good = not r["invented"]
            declined_ok += bool(good)
            if good and r["changed"]:
                # Counted, but it did not decline -- it reworded without
                # inventing. Worth seeing: the label used to say "declined
                # honestly 3/3" for a case that plainly changed the text.
                reworded.append(case["id"])
            mark = "" if good else " X"

        answered.add(r["model"])
        per_model[r["model"]] += 1
        secs = f"{r['latency_ms'] / 1000:.1f}" if r["latency_ms"] else "-"
        print(f"  {case['id']:<{width}}{case['expect']:<10}"
              f"{str(r['changed']):<9}{str(r.get('fixed', '-')):<7}"
              f"{str(r['clean']):<7}{crit:<8}{invented + mark:<10}{secs:<7}"
              f"{r['model']}")
        # "clean: False" on its own says a rewrite broke something without
        # saying what, which is not a finding anyone can act on. The ids are
        # already computed; printing them turns three identical-looking
        # misses into a diagnosis.
        if r.get("introduced"):
            print(f"  {'':<{width}}{'':<10}-> introduced "
                  f"{', '.join(sorted(r['introduced']))}")

    print("\nsummary")
    print(f"  fix rate           {fixed_ok}/{len(to_fix())}")
    blocked = fixed_ok - shipped_ok
    print(f"  REACHES THE USER   {shipped_ok}/{len(to_fix())}", end="")
    if blocked:
        print(f"   ({blocked} good rewrite(s) blocked by the critic -> 422)")
    else:
        print()
    # Renamed from "declined honestly". The measurement is "did not invent a
    # metric", which a case that reworded faithfully also satisfies -- so the
    # old label claimed a decline that had not happened.
    print(f"  resisted inventing {declined_ok}/{len(to_decline())}")
    if reworded:
        print(f"    (reworded, did not decline: {', '.join(reworded)})")
    print(f"  FABRICATIONS       {fabricated}   (must be 0)")
    if fabricated:
        print(f"    caught, contained {contained}   (never reaches the resume)")
        print(f"    REACHED THE USER {escaped}   (must be 0)")
    if statuses["degraded"]:
        print(f"  degraded           {statuses['degraded']} "
              "(the writer was never reached for these)")

    # The multi-provider design exists so the critic does not grade the
    # writer's own homework. A fallback can silently undo that, and a run in
    # which both ran on one model is measuring something weaker than intended.
    if args.llm and answered:
        print(f"  answered by        {', '.join(sorted(answered))}")
        critic_model = llm.resolve_chain("critique")[0]

        # A run split across models does not measure a model. The headline
        # above is a blend, and quoting it as "model X scores N/7" would be
        # the same error as naming the chain head: describing an intent
        # rather than what happened.
        if len(answered) > 1:
            print("\n  WARNING: this run was split across models, so the fix")
            print("  rate above is a BLEND and is not a score for any one of")
            print("  them. Cases per model:")
            for model, n in per_model.most_common():
                print(f"    {n:>2}  {model}")
            print("  A preferred model that fails mid-run is replaced silently")
            print("  by the next in the chain. Re-run when it is stable, or")
            print("  raise LLM_TIMEOUT_SECONDS, before quoting a number.")
        if answered == {critic_model}:
            independence_lost = True
            print(f"\n  WARNING: the writer answered on {critic_model}, which is")
            print("  also the critic's model. The intended independence between")
            print("  writer and critic collapsed -- most likely the preferred")
            print("  writing model failed and the chain fell through to it.")
            print("  Run `make models` to find out which ids actually work.")

    if statuses["ok"] == 0:
        print("\n  INCONCLUSIVE: nothing was measured. Every call degraded, so")
        print("  this reports on the transport, not on the writer. Fix that")
        print("  first -- a benchmark whose subject never ran scores nothing.")
        return 2
    if independence_lost:
        # Not a warning. The corpus measures a writer judged by an independent
        # critic; when the chain falls through onto the critic's own model
        # there is no independence left and the fix rate is a score for a
        # system we do not ship. A run that reported 6/7 and a run that
        # reported 3/7 differed by exactly this, and the second looked like a
        # regression until the chain line was read.
        print("\n  INCONCLUSIVE: writer and critic ran on the same model, so the")
        print("  numbers above do not describe the configured system. Point")
        print("  MODEL_WRITING at a model that is actually reachable and re-run.")
        return 2
    if escaped:
        print(f"\n  FAIL: {escaped} invented number(s) got past the critic and")
        print("  would appear in the user's resume. This outranks the fix rate:")
        print("  a writer that fixes everything by making facts up is worse than")
        print("  one that fixes nothing. Both layers failed on the same draft.")
        return 2
    # Floors are checked AFTER the fabrication gates on purpose: a writer
    # that fixes everything by inventing facts is worse than one that fixes
    # nothing, so `escaped` must outrank a good fix rate.
    #
    # LLM MODE ONLY. The offline rule writer fixes exactly 2 of 7 by design
    # -- it is a deterministic fallback, not a writer -- and `make
    # writer-report` exists to pin that number, so applying an LLM floor to
    # it would fail the build on correct behaviour. Caught by running it:
    # the first version of this gate turned the offline report red.
    n_fixable = len(to_fix())
    fix_rate = fixed_ok / n_fixable if n_fixable else 0.0
    reach_rate = shipped_ok / n_fixable if n_fixable else 0.0
    if args.llm and fix_rate < MIN_FIX_RATE:
        print(f"\n  FAIL: fix rate {fixed_ok}/{n_fixable} is below the floor of "
              f"{MIN_FIX_RATE:.0%} ({round(MIN_FIX_RATE * n_fixable)}/{n_fixable}).")
        print("  Zero fabrications is not an achievement on its own -- a writer")
        print("  that declines everything scores perfectly on every safety")
        print("  metric and is useless. This floor is what stops silence from")
        print("  passing.")
        return 2
    if args.llm and reach_rate < MIN_REACHES_USER:
        print(f"\n  FAIL: only {shipped_ok}/{n_fixable} fixes actually reached "
              f"the user, below the {MIN_REACHES_USER:.0%} floor.")
        print("  The critic is rejecting good rewrites. A fix the user never")
        print("  sees is a 422 to them, not a fix.")
        return 2
    if contained:
        print(f"\n  WARN: the writer invented a number in {contained} draft(s),")
        print("  and the critic rejected every one, so nothing false would have")
        print("  reached the resume. The safety net worked -- but it is the")
        print("  LAST line, not the only one. The writer prompt is still the")
        print("  thing that should not have produced a number at all.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
