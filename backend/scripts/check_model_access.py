"""Which models can this installation actually call?

    python -m scripts.check_model_access           # every configured provider
    python -m scripts.check_model_access --all     # also dump each catalogue

Listing a model is not the same as being entitled to call it, so for every
model in every chain this makes one real 1-token completion. That distinction
is the whole point: a key that authenticates but is not entitled returns 403,
not 401, and the two demand completely different fixes.

Exit codes
    0  every model in every task chain answered
    1  at least one model failed
    2  nothing is configured, or no model answered at all
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai import llm
from app.config import settings  # noqa: E402


def _client(provider: llm.Provider):
    import openai

    return openai.OpenAI(
        api_key=provider.api_key,
        base_url=provider.base_url,
        # Match production exactly. A 30s probe timeout against a 45s runtime
        # budget would report "timeout" for a model the app can actually use,
        # and this script's whole purpose is predicting runtime behaviour.
        timeout=float(settings.llm_timeout_seconds),
        max_retries=0,
    )


def _status_of(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None)


def _probe(provider: llm.Provider, bare: str) -> tuple[bool, str]:
    """One real completion. Returns (ok, detail)."""
    try:
        _client(provider).chat.completions.create(
            model=bare,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
        )
        return True, "answered"
    except Exception as exc:  # noqa: BLE001
        kind = llm.classify(exc)
        hint = ""
        if _status_of(exc) == 404 or "Not found for account" in str(exc):
            # The single most confusing error this project has produced. It is
            # not a typo in the model id and not a broken key: the model is
            # real and listed, and this account simply has no endpoint for it.
            hint = "  <- listed but NOT a Free Endpoint for this account"
        return False, f"{kind}: {str(exc)[:110]}{hint}"


# Substrings that mark a model as not a chat completion endpoint. Probing an
# embedding or rerank model with chat.completions returns a confusing 400 that
# says nothing about entitlement, so they are filtered out rather than failed.
_NON_CHAT = (
    "embed", "rerank", "reranking", "retrieval", "ocr", "whisper", "speech",
    "tts", "asr", "riva", "stable-diffusion", "sdxl", "flux", "clip",
    "vila", "parakeet", "canary", "codec", "nemoretriever", "guard",
)


def _is_chat_model(mid: str) -> bool:
    low = mid.lower()
    return not any(tok in low for tok in _NON_CHAT)


# Deliberately close to the real writing task: compose a resume summary from
# supplied facts. A 1-token ping measures queueing; this measures the work.
_GEN_PROMPT = (
    "Rewrite this resume summary in under 40 words, third person, no 'I', "
    "keeping every number exactly as given and inventing none:\n\n"
    "Platform engineer. Reduced CI pipeline runtime from 22 minutes to 9 "
    "minutes. Led 8 engineers. Ran workloads on AWS."
)


# A writing slot needs a general chat model. These families answer fast and
# look healthy on a latency table while being structurally incapable of the
# job: a safety classifier replies "User Safety: safe" in 0.5s and ranks
# FIRST on speed. That is exactly what happened -- this script recommended
# nemotron-3.5-content-safety as a writer.
_NOT_A_WRITER = re.compile(
    r"safety|guard|moderat|parse|ocr|embed|rerank|vision|transcri", re.I
)

# The facts the prompt supplies. Everything the model may legitimately say a
# number about lives in this string, so it doubles as the "resume" for the
# production fabrication guard.
_GEN_FACTS = (
    "Platform engineer. Reduced CI pipeline runtime from 22 minutes to 9 "
    "minutes. Led 8 engineers. Ran workloads on AWS."
)
_GEN_REQUIRED = ("22", "9")
_GEN_WORD_CAP = 40          # the prompt says "under 40 words"
_FIRST_PERSON = re.compile(r"\b(I|I'm|I've|my|me|we|we're|our|us)\b", re.I)
_PREAMBLE = re.compile(
    r"^\s*(here(?:'s| is)|sure|certainly|rewritten|revised|summary\s*:)", re.I
)


def _writer_blockers(bare: str, text: str) -> str | None:
    """Reason this model CANNOT hold a writing slot, or None.

    Disqualifying only. Quality defects are scored separately -- a model that
    writes clumsily is a worse choice; a model that fabricates or ignores the
    brief is not a choice at all.
    """
    if _NOT_A_WRITER.search(bare):
        return "not a writing model (classifier/parser/vision family)"
    if len(text.split()) < 12:
        return f"replied with {len(text.split())} words; asked for ~40"
    missing = [n for n in _GEN_REQUIRED if n not in text]
    if missing:
        return f"dropped supplied number(s) {', '.join(missing)}"

    # The production guard, not a second opinion invented for this script.
    # A model that invents a metric when handed four facts will do it to a
    # real resume, and fabrication outranks every other property here.
    from app.ai.agents import unverifiable_numbers

    invented = unverifiable_numbers(_GEN_FACTS, text)
    if invented:
        return f"FABRICATED number(s) {', '.join(sorted(invented))}"
    return None


def _writer_quality(text: str) -> list[str]:
    """Instruction-following defects, worst first. Ranked, not disqualifying.

    Every item is something the prompt explicitly asked for, so this measures
    whether a model does as it is told -- which is what the writing task is.
    """
    defects: list[str] = []
    words = text.split()
    if len(words) > _GEN_WORD_CAP:
        defects.append(f"{len(words)}w over {_GEN_WORD_CAP}w cap")
    if _FIRST_PERSON.search(text):
        defects.append("first person")
    if _PREAMBLE.search(text) or "```" in text:
        defects.append("preamble/markdown")
    if text[:1] in "\"'" and text.strip()[-1:] in "\"'":
        defects.append("wrapped in quotes")

    # Deliberately the app's OWN verb list, not a general notion of "strong
    # verb". ACTION_VERBS has 78 entries and real gaps -- "cut", "ran" and
    # "leading" are absent while "reduced" and "led" are present -- so a
    # model that writes "cut CI runtime" produces a rewrite the scorer will
    # not credit, and the finding stays open. That is a genuine reason to
    # prefer another writer, so it is scored; it is NOT a claim that the
    # prose is bad, and the label says so.
    from app.services.heuristics import ACTION_VERBS

    lowered = {w.strip(".,;:()").lower() for w in words}
    if not (lowered & {v.lower() for v in ACTION_VERBS}):
        defects.append("no ACTION_VERBS match (scorer will not credit)")
    return defects


def _resolved_for(task: str) -> str | None:
    """The model production will ACTUALLY use for a task, or None if the
    chain is empty.

    TASK_MODELS holds what is configured; resolve_chain holds what survives
    the free-tier and key filters. They differ more often than expected --
    a MODEL_* pinned in .env to a model that is not a free endpoint is
    dropped silently, and testing the configured value then measures a model
    that will never be called. This script once spent 45 seconds timing out
    such a model.
    """
    try:
        chain = llm.resolve_chain(task)
    except Exception:  # noqa: BLE001
        return None
    return chain[0] if chain else None


def _time_generation(qualified: str) -> tuple[bool, str]:
    """One realistic generation down the PRODUCTION path.

    Uses llm._client (LangChain ChatOpenAI) rather than a hand-rolled request,
    so this measures what the app will actually experience: the same timeout,
    the same reasoning_effort resolution, the same SDK.

    Two mistakes are deliberately avoided here.

    No max_tokens. Production sets none, and a cap is not a neutral
    simplification on a reasoning model: thinking tokens are drawn from the
    same budget, so a small cap is spent before any visible text and the
    reply comes back EMPTY. An earlier version of this function capped at 120
    and printed "OK 0 words" for every model -- a success line for a call
    that produced nothing.

    Empty output is a FAILURE. A generation test whose subject generated
    nothing has not passed.
    """
    effort = llm.effort_for("writing", qualified)
    start = time.monotonic()
    try:
        client = llm._client(qualified, reasoning_effort=effort)
        resp = client.invoke(_GEN_PROMPT)
    except Exception as exc:  # noqa: BLE001
        return False, f"{time.monotonic() - start:6.1f}s  {llm.classify(exc)}"

    elapsed = time.monotonic() - start
    text = (getattr(resp, "content", "") or "").strip()
    _, out_tokens = llm._usage(resp)
    words = len(text.split())
    effort_note = f"  effort={effort}" if effort else ""

    if not words:
        # Tokens billed but no prose means the model spent the reply on
        # reasoning. That is a real failure for a writing task.
        return False, (
            f"{elapsed:6.1f}s  EMPTY reply ({out_tokens} output tokens"
            f"){effort_note}"
        )

    bare = llm.split_model(qualified)[1]
    blocker = _writer_blockers(bare, text)
    if blocker:
        return False, f"{elapsed:6.1f}s  UNSUITABLE -- {blocker}"
    defects = _writer_quality(text)
    note = f"  quality: {'; '.join(defects)}" if defects else "  quality: clean"
    return True, (
        f"{elapsed:6.1f}s  {words:>3} words, {out_tokens} tokens{effort_note}{note}"
    )


def _probe_catalogue(name: str, provider, ids: set[str], sleep: float) -> int:
    """1-token call to every chat model `provider` advertises.

    This exists because a catalogue listing is not an entitlement. The models
    endpoint happily lists ids the account cannot call, and the only way to
    tell them apart is to call them. Guessing which ids work has already
    produced three invented model names and one report attributed to a model
    that never ran.
    """
    chat = sorted(m for m in ids if _is_chat_model(m))
    skipped = len(ids) - len(chat)
    print(f"\n--- probing {len(chat)} chat model(s) on {name} "
          f"({skipped} non-chat filtered out) ---")
    print(f"    one 1-token call each, {sleep}s apart -- "
          f"about {len(chat) * (sleep + 1.2) / 60:.0f} min\n")

    answered: list[tuple[float, str]] = []
    refused: dict[str, list[str]] = {}
    for i, bare in enumerate(chat, 1):
        start = time.monotonic()
        ok, detail = _probe(provider, bare)
        elapsed = time.monotonic() - start
        if ok:
            answered.append((elapsed, bare))
            print(f"  [{i:>3}/{len(chat)}] OK    {elapsed:>6.2f}s  {bare}")
        else:
            kind = detail.split(":", 1)[0]
            refused.setdefault(kind, []).append(bare)
            print(f"  [{i:>3}/{len(chat)}] --           {bare}  ({kind})")
        time.sleep(sleep)

    print(f"\n=== {name}: {len(answered)} of {len(chat)} advertised chat "
          f"models actually answer ===")
    for elapsed, bare in sorted(answered):
        print(f"  {elapsed:>6.2f}s  {name}:{bare}")
    if refused:
        print("\n  did not answer:")
        for kind, models in sorted(refused.items()):
            print(f"    {kind:<14} {len(models)}")
        print("\n  'error' with a 404 usually means listed-but-not-entitled.")
        print("  'timeout' means the model is real but too slow for a 45s budget.")
    if answered:
        print("\n  Pick MODEL_* ids from the OK list above -- those are "
              "measured, not advertised.")
        print("  Keep MODEL_WRITING and MODEL_CRITIQUE on different vendors.")
        # On a credit-less account "answers" and "has a free endpoint" are the
        # same thing, so this run IS the free list. Emitting it paste-ready
        # means the allowlist in llm.PROVIDERS can be refreshed from evidence
        # rather than from a screenshot that will go stale.
        print(f"\n  Paste-ready allowlist for PROVIDERS[{name!r}].free_models:")
        print("        free_models=frozenset({")
        for _, bare in sorted(answered, key=lambda pair: pair[1]):
            print(f'            "{bare}",')
        print("        }),")
    return 0 if answered else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all", action="store_true", help="dump every model each provider advertises"
    )
    parser.add_argument(
        "--probe",
        metavar="PROVIDER",
        help="send one real 1-token call to EVERY chat model this provider "
             "advertises and report which actually answer. Being listed is not "
             "the same as being entitled: kimi-k2.6 is in NVIDIA's catalogue "
             "and still 404s per-account. Takes a few minutes.",
    )
    parser.add_argument(
        "--generate-all", metavar="PROVIDER",
        help="time a REAL generation for EVERY free model of a provider, so a "
             "replacement writer is chosen on measured generation latency "
             "rather than on a 1-token ping.",
    )
    parser.add_argument(
        "--generate", action="store_true",
        help="time a REALISTIC generation (a ~40-word rewrite) for each task "
             "model, not a 1-token ping. A model can answer a 1-token call in "
             "0.4s and still time out writing a summary.",
    )
    parser.add_argument(
        "--probe-sleep", type=float, default=1.0,
        help="seconds between probe calls, to stay under the rate limit",
    )
    args = parser.parse_args()

    configured = {n: p for n, p in llm.PROVIDERS.items() if p.configured}
    missing = sorted(n for n, p in llm.PROVIDERS.items() if not p.configured)

    print("\n=== providers ===")
    for name, provider in llm.PROVIDERS.items():
        key = provider.api_key
        state = f"key set ({key[:6]}...{key[-4:]}, len {len(key)})" if key else "NO KEY"
        print(f"  {name:<10} {provider.base_url:<44} {state}")
    if missing:
        print(f"\n  not configured: {', '.join(missing)} "
              f"-- models on these are skipped, not failed.")

    if not configured:
        print("\nNo provider key is set. Add at least one to backend/.env:")
        print("  NVIDIA_API_KEY=...    # build.nvidia.com   (no card, ~40 req/min)")
        print("  GROQ_API_KEY=...      # console.groq.com   (no card, 30 req/min)")
        return 2

    # ---------------------------------------------------------------- catalogues
    listed: dict[str, set[str]] = {}
    for name, provider in configured.items():
        try:
            models = _client(provider).models.list()
            ids = {m.id for m in models.data}
            listed[name] = ids
            print(f"\n--- {name}: advertises {len(ids)} model(s) ---")
            if args.all:
                for mid in sorted(ids):
                    print(f"      {mid}")
        except Exception as exc:  # noqa: BLE001
            status = _status_of(exc)
            print(f"\n--- {name}: catalogue unavailable ({status or 'error'}) ---")
            print(f"      {str(exc)[:150]}")
            if status == 401:
                print(f"      401 => the {name} key is wrong, expired, or has "
                      "stray whitespace. Not an entitlement problem.")

    if args.generate_all:
        name = args.generate_all
        provider = llm.PROVIDERS.get(name)
        if provider is None:
            print(f"unknown provider {name!r}")
            return 2
        if not provider.configured:
            print(f"{name}: no key")
            return 2
        free = sorted(provider.free_models or ())
        print(f"\n--- {name}: real generation on {len(free)} free model(s) ---")
        print(f"    budget {settings.llm_timeout_seconds}s, no max_tokens, "
              "same call the app makes\n")
        rows: list[tuple[float, str, str]] = []
        for bare in free:
            qualified = f"{name}:{bare}"
            if _NOT_A_WRITER.search(bare):
                # Do not spend a call, and above all do not rank it.
                print(f"  SKIP  {bare:<46} not a writing model")
                continue
            ok, detail = _time_generation(qualified)
            print(f"  {'OK  ' if ok else 'FAIL'}  {bare:<46} {detail}")
            if ok:
                # Sort on DEFECT COUNT first, latency only as a tiebreak.
                # Ranking on speed alone is what put a safety classifier at
                # the top of this list.
                n_defects = 0 if "quality: clean" in detail else (
                    detail.split("quality: ")[1].count(";") + 1
                )
                rows.append((n_defects, float(detail.split("s")[0]), bare, detail))
            time.sleep(args.probe_sleep)
        if rows:
            rows.sort()
            print("\n  SUITABLE writers -- fewest instruction defects first,")
            print("  latency only as a tiebreak:")
            for n_def, secs, bare, det in rows:
                q = "clean" if not n_def else det.split("quality: ")[1]
                print(f"    {secs:6.1f}s  {n_def} defect(s)  {name}:{bare}")
                if n_def:
                    print(f"             {q}")
            print("\n  Prefer one from a different vendor to the critic")
            print(f"  ({llm.TASK_MODELS.get('critique')}), or the writer is")
            print("  judged by its own family.")
        else:
            print("\n  No model of this provider can generate inside the budget.")
        return 0 if rows else 1

    if args.generate:
        print("\n--- realistic generation (~40-word rewrite, uncapped) ---")
        print(f"    budget is LLM_TIMEOUT_SECONDS={settings.llm_timeout_seconds}s; "
              "a 1-token ping does not predict this")
        print("    no max_tokens, same as production -- a cap would be spent on "
              "reasoning\n")
        pairs: list[tuple[str, str]] = []   # (task, model production will use)
        for task in sorted(llm.TASK_MODELS):
            configured = llm.TASK_MODELS[task]
            resolved = _resolved_for(task)
            if resolved is None:
                print(f"  SKIP  {task:<12} no usable model in the chain")
                continue
            if resolved != configured:
                print(f"  NOTE  {task:<12} {configured}")
                print("        is configured but will NOT be used "
                      "(not a free endpoint, or no key).")
                print(f"        production resolves to {resolved}")
            pairs.append((task, resolved))

        wanted_gen = list(dict.fromkeys(m for _, m in pairs))
        slow: list[str] = []
        for qualified in wanted_gen:
            name_, bare = llm.split_model(qualified)
            provider = llm.PROVIDERS.get(name_)
            if provider is None or not provider.configured:
                print(f"  SKIP  {qualified:<44} no key")
                continue
            ok, detail = _time_generation(qualified)
            print(f"  {'OK  ' if ok else 'FAIL'}  {qualified:<44} {detail}")
            if not ok:
                slow.append(qualified)
        if slow:
            print("\n  These cannot do real work inside the current budget.")
            print("  Either raise LLM_TIMEOUT_SECONDS or pick another model --")
            print("  a chain entry that times out costs every caller the full")
            print("  budget before falling through.")
            return 1
        return 0

    if args.probe:
        target = args.probe
        if target not in llm.PROVIDERS:
            print(f"\nUnknown provider {target!r}. "
                  f"Known: {', '.join(llm.PROVIDERS)}")
            return 2
        if target not in configured:
            print(f"\n{target} has no API key set -- nothing to probe.")
            return 2
        if target not in listed:
            print(f"\nCould not list {target}'s catalogue (see above), "
                  "so there is nothing to probe.")
            return 2
        return _probe_catalogue(target, llm.PROVIDERS[target], listed[target],
                                args.probe_sleep)

    # ------------------------------------------------------- real calls per model
    wanted: list[str] = list(
        dict.fromkeys(list(llm.TASK_MODELS.values()) + llm.FALLBACK_CHAIN)
    )
    print("\n--- one real 1-token call per model used by any task ---")
    reachable: list[str] = []
    failed: list[tuple[str, str]] = []
    skipped: list[str] = []

    for qualified in wanted:
        name, bare = llm.split_model(qualified)
        provider = llm.PROVIDERS[name]
        if not provider.configured:
            skipped.append(qualified)
            print(f"  SKIP  {qualified:<44} no {name} key")
            continue

        advertised = listed.get(name)
        hint = ""
        if advertised is not None and bare not in advertised:
            hint = "  (not in its catalogue!)"

        ok, detail = _probe(provider, bare)
        if ok:
            reachable.append(qualified)
            print(f"  OK    {qualified:<44} {detail}")
        else:
            failed.append((qualified, detail, bool(hint)))
            print(f"  FAIL  {qualified:<44} {detail}{hint}")

    # ------------------------------------------------------------------- verdict
    print("\n=== verdict ===")
    drifted: list[str] = []
    for task, model in sorted(llm.TASK_MODELS.items()):
        mark = "ok" if model in reachable else ("skipped" if model in skipped else "FAIL")
        print(f"  {task:<12} {model:<44} {mark}")
        resolved = _resolved_for(task)
        if resolved is not None and resolved != model:
            drifted.append(task)
            print(f"  {'':<12} -> production uses {resolved}")

    if drifted:
        print(f"\n  {len(drifted)} task(s) do not use their configured model: "
              f"{', '.join(drifted)}.")
        print("  A MODEL_* in .env that is not a free endpoint is dropped before")
        print("  the request is sent, so the FAIL above is for a model nothing")
        print("  will call. Update .env rather than chasing the failure.")

    if reachable:
        print(f"\n  {len(reachable)} model(s) reachable.")
    if failed:
        print(f"  {len(failed)} model(s) failed.")
        # Say which kind of failure it is. Telling someone to fix their model
        # ids when the real problem is the key is the same mistake as telling
        # them their resume needs a number when the writer was rate-limited.
        auth = [q for q, d, _ in failed if "401" in d or "403" in d]
        unknown_id = [q for q, _, miss in failed if miss]
        if auth and len(auth) == len(failed):
            print("  Every failure was 401/403 -- this is the KEY, not the model ids.")
            print("  Check the key is pasted whole, unexpired, and for this provider.")
        elif unknown_id:
            print("  Some ids are not in the provider's catalogue. Free catalogues")
            print("  churn, so names go stale: re-run with --all and copy a real")
            print("  id into the MODEL_* settings in backend/.env.")
        else:
            print("  Mixed causes -- read the per-model detail above.")
    if not reachable:
        print("\n  Nothing answered. Fix the transport before trusting any")
        print("  quality measurement -- a benchmark whose subject never ran")
        print("  reports a verdict about nothing.")
        return 2
    if failed:
        return 1

    print("\n  Every model in every task chain answered.")
    print("  Next: python -m scripts.critic_report --llm --sleep 2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
