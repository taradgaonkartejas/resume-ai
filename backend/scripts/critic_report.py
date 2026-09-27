"""Score the critic against the labelled corpus and print a table.

    python -m scripts.critic_report                    # rule engine only
    python -m scripts.critic_report --llm              # also the live LLM critic
    python -m scripts.critic_report --llm --sleep 60   # pace it past the rate limit

The --llm run needs UNOROUTER_API_KEY. Use it to answer the question the test
suite cannot answer offline: does TASK_EFFORT["critique"] = "minimal" keep the
critic as strict as it was?

The free tier allows one request per minute per model per account, so an
unpaced run throttles after the first few cases and every later case degrades
to rules. --sleep trades wall-clock time for a result that means something:
18 cases at the default 60s is roughly 18 minutes. Slow and true beats fast
and meaningless -- an unpaced run once reported "the LLM critic is adding
nothing" when the LLM had not run at all.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai import llm  # noqa: E402
from app.ai.agents import _rule_critique, review_draft  # noqa: E402
from tests.critic_cases import CASES, by_kind, fabrications, faithful  # noqa: E402


def rule_approves(case: dict) -> bool:
    return _rule_critique(case["original"], {"suggested_text": case["suggested"]}).approved


def llm_approves(case: dict) -> tuple[bool, str]:
    data = {
        "experience": [
            {"company": "Acme", "role": "Eng", "dates": "", "bullets": [case["original"]]}
        ]
    }
    outcome = review_draft(
        {
            "target_ref": "exp_0.bullet_0",
            "original_text": case["original"],
            "suggested_text": case["suggested"],
            "keywords": [],
        },
        data,
    )
    return outcome.value.approved, outcome.status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true", help="also score the live LLM critic")
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="pause between cases to stay under the free-tier rate limit (try 60)",
    )
    args = parser.parse_args()

    use_llm = args.llm
    if use_llm and not llm.is_configured():
        print("UNOROUTER_API_KEY is not set -- cannot score the LLM critic.")
        return 2

    effort = llm.effort_for("critique")
    model = llm.resolve_chain("critique")[0] if use_llm else "-"
    print(f"\ncritic corpus: {len(CASES)} cases "
          f"({len(fabrications())} fabrications, {len(faithful())} faithful)")
    if use_llm:
        print(f"model: {model}   reasoning_effort: {effort or 'provider default'}")

    width = max(len(c["id"]) for c in CASES) + 2
    header = f"{'case':<{width}}{'kind':<11}{'want':<9}{'rules':<9}"
    if use_llm:
        header += f"{'llm':<9}"
    print("\n" + header)
    print("-" * len(header))

    degraded = 0
    llm_hits = llm_false = 0
    statuses: Counter[str] = Counter()
    for position, case in enumerate(CASES):
        if use_llm and args.sleep and position:
            time.sleep(args.sleep)
        want = case["expect"]
        rules = "approve" if rule_approves(case) else "reject"
        row = f"{case['id']:<{width}}{case['kind']:<11}{want:<9}"
        row += f"{rules + ('' if rules == want else ' X'):<9}"
        if use_llm:
            approved, status = llm_approves(case)
            statuses[status] += 1
            if status == "degraded":
                degraded += 1
            verdict = "approve" if approved else "reject"
            hit = verdict == want
            if want == "reject" and hit:
                llm_hits += 1
            if want == "approve" and not hit:
                llm_false += 1
            row += f"{verdict + ('' if hit else ' X'):<9}"
        print(row)

    rule_hits = sum(1 for c in fabrications() if not rule_approves(c))
    rule_false = sum(1 for c in faithful() if not rule_approves(c))
    n_fab, n_ok = len(fabrications()), len(faithful())

    print("\nsummary")
    print(f"  rule engine   recall {rule_hits}/{n_fab}   false rejections {rule_false}/{n_ok}")
    if use_llm:
        # If EVERY call degraded, the LLM never ran and the "llm" column is just
        # the rule engine wearing a different hat. Reporting that as a quality
        # verdict would be a lie: the honest answer is that we learned nothing.
        if degraded == len(CASES):
            print("  llm critic    DID NOT RUN \u2014 every call fell back to rules")
            print(f"\n  INCONCLUSIVE: all {degraded} calls degraded, so this run says")
            print("  NOTHING about LLM critic quality. The numbers above are the rule")
            print("  engine scored twice.")
            print("\n  Diagnose the transport first:")
            print("      python -m scripts.check_model_access")
            return 2

        print(f"  llm critic    recall {llm_hits}/{n_fab}   false rejections {llm_false}/{n_ok}")

        # How the answers arrived matters as much as what they were. A run
        # carried by prose salvage is a working critic on a broken transport,
        # and must not be reported as though json_schema were honoured.
        salvaged = statuses["ok-json"] + statuses["ok-prose"]
        if salvaged:
            print(
                f"  transport     {statuses['ok']} via json_schema, "
                f"{statuses['ok-json']} via embedded JSON, "
                f"{statuses['ok-prose']} via prose salvage"
            )
            print("  The provider is ignoring json_schema; salvage is carrying the run.")
        if degraded:
            print(f"  WARNING: {degraded}/{len(CASES)} call(s) degraded to the rule engine")
            print("  Partial degradation \u2014 treat the llm column as a LOWER BOUND.")
        if llm_hits <= rule_hits:
            print("\n  The LLM critic is adding nothing over the rule engine.")
            print("  Check TASK_EFFORT['critique'] and structured-output support.")
            return 1

        if llm_false > rule_false:
            print(
                f"\n  NOTE: false rejections rose {rule_false}/{n_ok} -> "
                f"{llm_false}/{n_ok}. The LLM critic is stricter, not just better."
            )
            print("  Check whether those cases are model errors or corpus labels.")
        semantic = by_kind("semantic")
        print(f"\n  semantic gap closed: {llm_hits - rule_hits}/{len(semantic)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
