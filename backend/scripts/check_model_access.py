"""Which models can this API key actually reach?

    python -m scripts.check_model_access
    python -m scripts.check_model_access --all     # list every model on offer

A 403 "this token has no access to model X" is NOT a quality problem and not a
bug in our code: the key authenticates (or it would be 401) but is not entitled
to that model. This script separates the three possibilities that a 403 leaves
open:

    1. the model name is wrong or has been renamed upstream
    2. the account has not enabled / accepted terms for free models
    3. the key is scoped to a different set of models

It asks the provider what it offers, then makes ONE real one-token completion
per model in our fallback chain, because /models listing a model does not prove
the key may call it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai import llm  # noqa: E402
from app.config import settings  # noqa: E402


def _client():
    import openai

    return openai.OpenAI(
        api_key=settings.unorouter_api_key,
        base_url=settings.unorouter_base_url,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="list every model the provider advertises")
    args = ap.parse_args()

    key = settings.unorouter_api_key or ""
    if not key:
        print("No UNOROUTER_API_KEY set. Nothing to check.")
        return 2

    print(f"base_url: {settings.unorouter_base_url}")
    print(f"key:      {key[:6]}...{key[-4:]}  ({len(key)} chars)")

    client = _client()

    # ---- 1. what does the provider say it offers? ----------------------
    offered: set[str] = set()
    list_status: int | None = None
    print("\n--- GET /models ---")
    try:
        listing = client.models.list()
        offered = {m.id for m in listing.data}
        print(f"the key can LIST {len(offered)} models")
        if args.all:
            for mid in sorted(offered):
                print(f"    {mid}")
    except Exception as exc:  # noqa: BLE001
        list_status = getattr(exc, "status_code", None)
        print(f"listing failed ({list_status}): {type(exc).__name__}: "
              f"{str(exc)[:160]}")

    # ---- 2. can it actually CALL the ones we depend on? ----------------
    chain = list(dict.fromkeys(llm.FALLBACK_CHAIN))
    print("\n--- one real 1-token call per model in FALLBACK_CHAIN ---")
    reachable: list[str] = []
    for model in chain:
        listed = "listed" if model in offered else "NOT listed"
        try:
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            detail = str(exc).replace("\n", " ")[:110]
            print(f"  FAIL  {model:<34} [{listed}]  {name}: {detail}")
            continue
        reachable.append(model)
        print(f"  OK    {model:<34} [{listed}]")

    # ---- 3. say what it means, plainly --------------------------------
    print("\nverdict")
    if reachable:
        print(f"  {len(reachable)}/{len(chain)} of the fallback chain is reachable:")
        for m in reachable:
            print(f"      {m}")
        print("  The LLM path can run. Re-run: python -m scripts.critic_report --llm")
        return 0

    print("  NONE of the fallback chain is reachable.")
    if offered:
        free = sorted(m for m in offered if m.endswith(":free"))
        print(f"\n  The key CAN list {len(offered)} models, "
              f"{len(free)} of them ':free'.")
        if free:
            print("  Free models this key lists (first 15):")
            for m in free[:15]:
                print(f"      {m}")
            print("\n  If our four are absent from this list, they were renamed or "
                  "withdrawn upstream.\n  Pick replacements from here and set "
                  "model_default + FALLBACK_CHAIN in app/ai/llm.py.")
        else:
            print("  None of them are ':free' \u2014 this key likely has no free tier.")
    elif list_status == 401:
        print("\n  The key was REJECTED outright (401). This is not an entitlement\n"
              "  problem \u2014 the key is wrong, expired, or has stray whitespace.\n"
              "  Check UNOROUTER_API_KEY in backend/.env.")
    else:
        print(f"\n  The key could not list models ({list_status}) AND cannot call any\n"
              "  model, yet it is not a 401 \u2014 so the key is real but entitled to\n"
              "  nothing. That is an account-level setting, not a code bug: enable\n"
              "  free models / accept their terms at https://unorouter.com.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
