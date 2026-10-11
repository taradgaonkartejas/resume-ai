"""Score the ATS agent's acronym expansions against the pinned corpus.

    python -m scripts.ats_report              # offline: gate only
    python -m scripts.ats_report --llm        # live
    python -m scripts.ats_report --llm --runs 3    # stability

Thresholds live here rather than in the test suite because the decision is
an aggregate one: a single unlucky judgement should not turn the build red,
but a pattern of them should stop the feature shipping.

**The baseline is silence.** With no provider key the agent says nothing, so
the question is never "is it good?" but "is it better than nothing?". A
wrong expansion puts a credential on someone's resume that they do not hold,
which is worse than no advice -- hence the fabrication threshold is zero and
is the kill criterion.

This report used to also score section relabelling. On 2026-10-11 that half
returned a 17% false-positive rate against a pre-registered maximum of 10%
and was deleted rather than retuned. See `tests/ats_cases.py`.
"""

from __future__ import annotations

import argparse
import sys
import time

from app.ai import agents, llm
from app.services import ats
from tests.ats_cases import EXPANSIONS, expansion_resume

# Pre-registered. Written before the first live run; change them only with a
# reason recorded next to the change, never to accommodate a result.
MAX_FABRICATED_EXPANSIONS = 0    # kill criterion: same bar as the writer

# NOT a quality bar -- a vacuity guard. Silence scores zero fabrications, so
# without a floor on correct answers the kill criterion is trivially passed
# by an agent that never speaks, and a test that cannot fail is worse than no
# test. Half the corpus is deliberately lax: silence on any single acronym is
# still acceptable.
MIN_CORRECT_RATE = 0.50


def _run(acronyms: list[str], sleep: float) -> dict:
    try:
        out = agents.review_ats(expansion_resume(acronyms))
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:120]}
    if sleep:
        time.sleep(sleep)
    return {
        "got": {e["acronym"]: e["expanded"].lower() for e in out.value["expansions"]},
        "used_llm": out.used_llm,
        "model": out.model,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--llm", action="store_true", help="call a real provider")
    ap.add_argument("--runs", type=int, default=1, help="repeat; judgement varies")
    ap.add_argument("--sleep", type=float, default=0.0, help="seconds between calls")
    args = ap.parse_args()

    real = [e for e in EXPANSIONS if not e.get("decoy")]
    decoys = [e["acronym"] for e in EXPANSIONS if e.get("decoy")]

    # ------------------------------------------------------------ gate first
    # The gate's recall is a hard ceiling on the agent's: an acronym filtered
    # out deterministically is one the model never gets a chance at.
    print(f"\nats corpus: {len(real)} acronyms + {len(decoys)} decoys")
    reach = set(ats.bare_acronyms(expansion_resume([e["acronym"] for e in real])))
    missed = [e["acronym"] for e in real if e["acronym"] not in reach]
    leaked = ats.bare_acronyms(expansion_resume(decoys))
    print(f"  gate: {len(reach)}/{len(real)} real acronyms reach the agent "
          f"(recall ceiling)")
    if missed:
        print(f"        UNWINNABLE -- filtered before the model: {', '.join(missed)}")
    print(f"        {len(leaked)}/{len(decoys)} decoys reach the agent "
          f"(want 0; they are already the normal name)")
    if leaked:
        print(f"        GATE REGRESSION: {', '.join(leaked)}")

    if not args.llm:
        print("\noffline mode: the gate and guard are covered by "
              "tests/test_ats_corpus.py.")
        print("Re-run with --llm to score factual accuracy.\n")
        return 1 if (missed or leaked) else 0

    if not llm.is_configured():
        print("\nNo provider API key is set -- cannot score the live agent.\n")
        return 1

    # ------------------------------------------------------------- judgement
    width = max(len(e["acronym"]) for e in EXPANSIONS) + 2
    print(f"\n{'acronym':<{width}}{'verdict':<12}{'runs':<8}expansion")
    print("-" * (width + 60))

    tally = {"right": [], "wrong": [], "missing": []}
    degraded = 0
    for item in real:
        results = [_run([item["acronym"]], args.sleep) for _ in range(args.runs)]
        errors = [r for r in results if "error" in r]
        if errors:
            print(f"  {item['acronym']:<{width}}ERROR {errors[0]['error']}")
            tally["missing"].append(item["acronym"])
            continue
        # A call that fell back to the rule engine says nothing, which would
        # otherwise score as a harmless "missing". It is not a measurement at
        # all, and counting it as one is how a rate limit quietly becomes a
        # passing grade.
        degraded += sum(1 for r in results if not r["used_llm"])

        verdicts, texts = [], []
        for res in results:
            text = res["got"].get(item["acronym"])
            texts.append(text)
            if text is None:
                verdicts.append("missing")
            elif any(a in text for a in item["accept"]):
                verdicts.append("right")
            else:
                verdicts.append("wrong")
        # Worst verdict wins: one fabrication in three runs is a fabrication.
        verdict = ("wrong" if "wrong" in verdicts
                   else "right" if "right" in verdicts else "missing")
        agree = verdicts.count(verdict)
        flag = "" if agree == args.runs else "  <- UNSTABLE"
        shown = next((t for t in texts if t), "-")
        print(f"  {item['acronym']:<{width}}{verdict:<12}{agree}/{args.runs:<6}"
              f"{shown[:48]}{flag}")
        tally[verdict].append(item["acronym"])

    # Decoys: the right answer is silence.
    decoy_fired = []
    for token in decoys:
        res = _run([token], args.sleep)
        if "error" not in res and token in res["got"]:
            decoy_fired.append(token)

    # --------------------------------------------------------------- verdict
    correct_rate = len(tally["right"]) / len(real) if real else 0.0
    fabricated = len(tally["wrong"]) + len(decoy_fired)

    print("\nsummary")
    print(f"  correct   {len(tally['right'])}  {', '.join(tally['right']) or '-'}")
    print(f"  missing   {len(tally['missing'])}  "
          f"{', '.join(tally['missing']) or '-'}   (silence is acceptable)")
    print(f"  WRONG     {len(tally['wrong'])}  "
          + (", ".join(tally["wrong"]) or "-"))
    if decoy_fired:
        print(f"  DECOYS EXPANDED  {', '.join(decoy_fired)}")
    if degraded:
        print(f"  degraded  {degraded} call(s) never reached a model "
              "-- those are not measurements")
    print(f"\n  fabricated expansions  {fabricated}  "
          f"(max {MAX_FABRICATED_EXPANSIONS})  <- kill criterion")
    print(f"  correct rate           {correct_rate:.0%}  "
          f"(min {MIN_CORRECT_RATE:.0%}, vacuity guard)")

    failures = []
    if fabricated > MAX_FABRICATED_EXPANSIONS:
        failures.append("fabricated expansions -- silence is better than this agent")
    if correct_rate < MIN_CORRECT_RATE:
        failures.append("correct rate -- the agent is too quiet to be worth a call")
    if leaked:
        failures.append("gate regression: decoys reached the model")
    if degraded:
        failures.append(f"{degraded} degraded call(s) -- result is not trustworthy")

    if failures:
        print("\n  FAIL: " + "; ".join(failures))
        if fabricated:
            print("  A wrong expansion is a credential the user does not hold."
                  " Delete the feature rather than retune until it passes.")
        return 1
    print("\n  PASS: the agent earns its place over silence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
