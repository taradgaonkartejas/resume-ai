"""Score the ATS agent's judgement against the labelled corpus.

    python -m scripts.ats_report              # offline: gate + guard only
    python -m scripts.ats_report --llm        # live, dev split
    python -m scripts.ats_report --llm --holdout   # the one that counts
    python -m scripts.ats_report --llm --runs 3    # stability

Thresholds live here rather than in the test suite because the decision is
an aggregate one: a single unlucky judgement should not turn the build red,
but a pattern of them should stop the feature shipping.

**The baseline is silence.** With no provider key the agent says nothing, so
the question is never "is it good?" but "is it better than nothing?". A false
positive costs the user a hand-edit on a document that was already correct,
plus some trust in every other suggestion the product makes. That is worse
than no advice, which is why the false-positive threshold is the kill
criterion and the others are not.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict

from app.ai import agents, llm
from app.services import ats
from tests.ats_cases import (
    CASES,
    DEV,
    EXPANSIONS,
    HOLDOUT,
    expansion_resume,
    negatives,
    positives,
    resume_for,
)

# Pre-registered. Written before the first live run; change them only with a
# reason recorded next to the change, never to accommodate a result.
MAX_FALSE_POSITIVE_RATE = 0.10   # kill criterion: above this, silence wins
MIN_RECALL = 0.75
MIN_TARGET_ACCURACY = 0.90
MAX_FABRICATED_EXPANSIONS = 0    # same bar as the writer


def _judge(case: dict) -> dict:
    """Run one case. Returns what fired and whether it was right."""
    try:
        out = agents.review_ats(resume_for(case))
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:80]}
    relabels = out.value["relabels"]
    fired = bool(relabels)
    target = relabels[0]["suggested_title"] if fired else ""
    if case["expect"] == "leave":
        correct = not fired
    else:
        correct = fired and target == case["expect_target"]
    return {
        "fired": fired,
        "target": target,
        "correct": correct,
        "used_llm": out.used_llm,
        "model": out.model,
    }


def _expansion_check(sleep: float) -> dict:
    """Factual accuracy, not judgement. Wrong expansions are fabrications."""
    real = [e for e in EXPANSIONS if not e.get("decoy")]
    decoys = [e["acronym"] for e in EXPANSIONS if e.get("decoy")]

    leaked = ats.bare_acronyms(expansion_resume(decoys))
    try:
        out = agents.review_ats(expansion_resume([e["acronym"] for e in real]))
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:80], "leaked_decoys": leaked}
    time.sleep(sleep)

    got = {e["acronym"]: e["expanded"].lower() for e in out.value["expansions"]}
    right, wrong, missing = [], [], []
    for item in real:
        text = got.get(item["acronym"])
        if text is None:
            missing.append(item["acronym"])
        elif any(a in text for a in item["accept"]):
            right.append(item["acronym"])
        else:
            wrong.append((item["acronym"], got[item["acronym"]]))
    return {
        "right": right, "wrong": wrong, "missing": missing,
        "leaked_decoys": leaked, "used_llm": out.used_llm,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="call a real provider")
    ap.add_argument("--holdout", action="store_true", help="score the held-out split")
    ap.add_argument("--all", action="store_true", help="score every case (reporting only)")
    ap.add_argument("--runs", type=int, default=1, help="repeat each case; judgement varies")
    ap.add_argument("--sleep", type=float, default=0.0, help="seconds between calls")
    args = ap.parse_args()

    if args.all:
        cases, split = CASES, "ALL (do not quote as a result)"
    elif args.holdout:
        cases, split = HOLDOUT, "HOLDOUT"
    else:
        cases, split = DEV, "dev"

    # ---------------------------------------------------------- gate first
    print(f"\nats corpus: {len(cases)} cases   split: {split}")
    gate_misses = [c["id"] for c in positives(cases) if not ats.mislabelled_sections(resume_for(c))]
    reaching = [c["id"] for c in negatives(cases) if ats.mislabelled_sections(resume_for(c))]
    print(f"  gate: {len(positives(cases)) - len(gate_misses)}/{len(positives(cases))} "
          f"positives reach the agent (recall ceiling)")
    if gate_misses:
        print(f"        UNWINNABLE -- filtered before the model: {', '.join(gate_misses)}")
    print(f"        {len(reaching)}/{len(negatives(cases))} negatives reach the agent "
          f"(the rest are filtered deterministically)")

    if not args.llm:
        print("\noffline mode: the gate and guard are covered by tests/test_ats_corpus.py.")
        print("Re-run with --llm to score judgement.\n")
        return 0

    if not llm.is_configured():
        print("\nNo provider API key is set -- cannot score the live agent.\n")
        return 1

    # ---------------------------------------------------------- judgement
    width = max(len(c["id"]) for c in cases) + 2
    print(f"\n{'case':<{width}}{'expect':<10}{'fired':<8}{'target':<22}{'runs'}")
    print("-" * (width + 48))

    tally: dict[str, list] = defaultdict(list)
    for case in cases:
        results = []
        for _ in range(args.runs):
            results.append(_judge(case))
            if args.sleep:
                time.sleep(args.sleep)
        errors = [r for r in results if "error" in r]
        if errors:
            print(f"  {case['id']:<{width}}ERROR {errors[0]['error']}")
            continue
        ok = sum(1 for r in results if r["correct"])
        last = results[-1]
        flag = "" if ok == args.runs else ("  <- UNSTABLE" if ok else "  <- WRONG")
        print(f"  {case['id']:<{width}}{case['expect']:<10}"
              f"{('yes' if last['fired'] else 'no'):<8}{(last['target'] or '-'):<22}"
              f"{ok}/{args.runs}{flag}")
        tally[case["expect"]].append((case, ok))

    # ----------------------------------------------------------- expansions
    print("\nexpansions (fact, not judgement)")
    exp = _expansion_check(args.sleep)
    if "error" in exp:
        print(f"  ERROR {exp['error']}")
        fabricated = 0
    else:
        fabricated = len(exp["wrong"])
        print(f"  correct   {len(exp['right'])}  {', '.join(exp['right']) or '-'}")
        print(f"  missing   {len(exp['missing'])}  {', '.join(exp['missing']) or '-'}"
              "   (silence is acceptable)")
        print(f"  WRONG     {fabricated}  "
              + (", ".join(f"{a} -> {t}" for a, t in exp["wrong"]) or "-"))
        if exp["leaked_decoys"]:
            print(f"  decoys reached the model: {exp['leaked_decoys']}  <- gate regression")

    # -------------------------------------------------------------- verdict
    pos, neg = tally["relabel"], tally["leave"]
    recall = sum(1 for _c, ok in pos if ok == args.runs) / len(pos) if pos else 0.0
    fp = sum(1 for _c, ok in neg if ok < args.runs)
    fp_rate = fp / len(neg) if neg else 0.0
    fired_pos = [(c, ok) for c, ok in pos if ok]
    target_acc = (sum(1 for _c, ok in fired_pos if ok == args.runs) / len(fired_pos)
                  if fired_pos else 0.0)

    print("\nsummary")
    print(f"  recall on positives      {recall:.0%}  (min {MIN_RECALL:.0%})")
    print(f"  FALSE POSITIVES          {fp}/{len(neg)} = {fp_rate:.0%}  "
          f"(max {MAX_FALSE_POSITIVE_RATE:.0%})  <- kill criterion")
    print(f"  correct target when fired {target_acc:.0%}  (min {MIN_TARGET_ACCURACY:.0%})")
    print(f"  fabricated expansions    {fabricated}  (max {MAX_FABRICATED_EXPANSIONS})")

    failures = []
    if fp_rate > MAX_FALSE_POSITIVE_RATE:
        failures.append("false-positive rate -- silence is better than this agent")
    if recall < MIN_RECALL:
        failures.append("recall")
    if target_acc < MIN_TARGET_ACCURACY:
        failures.append("target accuracy")
    if fabricated > MAX_FABRICATED_EXPANSIONS:
        failures.append("fabricated expansions")

    if failures:
        print("\n  FAIL: " + "; ".join(failures))
        if "false-positive rate" in failures[0]:
            print("  The honest response is to delete the relabel half, not to"
                  " retune until it passes.")
        return 1
    print("\n  PASS: the agent earns its place over silence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
