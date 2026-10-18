# `backend/.env` — what to keep

Your current `.env` is **stale and points at the provider you excluded**. It sets
`UNOROUTER_API_KEY`, `UNOROUTER_BASE_URL`, `MODEL_DEFAULT=gemini-…` and
`EMBEDDING_MODEL=gemini-…`, and sets **no `NVIDIA_API_KEY` and no `GROQ_API_KEY`**.
So every task currently resolves to the Gemini model that was dropped on privacy
grounds. This is `NEXT.md` item 1, still outstanding.

## The rule

`config.py` already holds every model decision **with the reasoning next to it**.
A line in `.env` that merely repeats a default is a line that can drift out of
sync silently. So keep in `.env` only:

1. secrets, and
2. values that genuinely differ from the default.

Delete everything else and let `config.py` be the single source of truth.

## Keep

This is the whole file. It is ordered the same way as `.env.example`, which is
now pinned to the `config.py` defaults by `tests/test_env_example_parity.py` —
so the fastest way to produce it is `Copy-Item .env.example .env` and then fill
in the three keys.

```ini
# --- secrets: the only things that MUST be here ---
NVIDIA_API_KEY=<build.nvidia.com>
GROQ_API_KEY=<console.groq.com>
# Serves MODEL_ATS and MODEL_WRITING only. Read "Before you set
# CODECRAFT_API_KEY" below — this provider does not disclose its upstream.
CODECRAFT_API_KEY=<codecraftapi.com>

# --- models: all eight, so any one can be swapped without touching source ---
# Every value equals the config.py default. Writer and critic MUST stay on
# different vendors — a writer graded by its own family is not an independent
# measurement, and a test enforces it.
#
# VERIFIED 2026-10-11, superseding the "not yet verified" note that stood
# here: codecraft accepts `json_schema` despite documenting only
# `json_mode` (all 30 working models passed a live probe), and
# `writer-report-llm` returned 7/7 fix rate, 0 fabrications, answered by
# codecraft:claude-sonnet-5 alone. See "SETTLED" below.
MODEL_DEFAULT=groq:openai/gpt-oss-120b
MODEL_WRITING=codecraft:claude-sonnet-5
MODEL_CHAT=groq:openai/gpt-oss-120b
MODEL_CRITIQUE=groq:openai/gpt-oss-120b
MODEL_EXTRACTION=groq:openai/gpt-oss-120b
MODEL_JD_ANALYSIS=groq:openai/gpt-oss-120b
MODEL_SCORING=groq:openai/gpt-oss-20b
MODEL_ATS=codecraft:deepseek-v4-pro-0813

# Known-good alternatives, measured 2026-10-11, if codecraft disappoints:
#   MODEL_WRITING=nvidia:meta/muse-glimmer-30b     5.2s, 0 defects
#   MODEL_ATS=groq:openai/gpt-oss-120b             previous default

# --- embeddings ---
# minilm = local ONNX all-MiniLM-L6-v2. Needs a one-time `make fetch-models`.
# EMBEDDING_DIM is load-bearing: app/models.py builds the column as
# Vector(settings.embedding_dim) at import time. Changing either of the two
# lines below requires `python -m app.cli db reset`.
EMBEDDING_BACKEND=minilm
EMBEDDINGS_REMOTE=false
EMBEDDING_DIM=384

# --- local infrastructure (machine-specific, so it belongs here) ---
DATABASE_URL=postgresql+psycopg2://admin:Admin123@localhost:5433/resumeai
ALLOW_SQLITE_FALLBACK=false
# These MUST equal MINIO_ROOT_USER / MINIO_ROOT_PASSWORD in
# docker-compose.yml, which are `minio` / `minio123` — NOT MinIO's stock
# default credentials. A mismatch does not raise: ALLOW_DISK_FALLBACK=true
# catches it and writes to local disk, so uploads keep working and the
# bucket stays empty. If MinIO looks ignored, check these two lines first.
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY=minio
S3_SECRET_KEY=minio123
S3_BUCKET_UPLOADS=resumeai-uploads
S3_BUCKET_EXPORTS=resumeai-exports
ALLOW_DISK_FALLBACK=true

# --- Redis + LLM response cache ---
# compose ships redis:7-alpine on 6379 (RedisInsight on 5540). Keep the URL
# even while the cache is off, so it can be flipped without editing source.
# Every Redis error degrades to a cache miss; the app runs with Redis stopped.
REDIS_URL=redis://localhost:6379/0
LLM_CACHE_ENABLED=false

# --- app ---
FREE_MODELS_ONLY=true
# 45, NOT 90. This is PER MODEL and the chain tries up to four of them. The
# browser aborts an LLM request at LLM_REQUEST_TIMEOUT_MS
# (frontend/src/lib/env.ts). A stale .env carrying LLM_TIMEOUT_SECONDS=90
# against the old 90s client budget meant the browser gave up at the exact
# instant the FIRST model's timeout expired, so no fallback was ever
# reachable: "Request timed out after 90000 ms" on Create tailored resume,
# while the backend was still working. tests/test_chain_latency_budget.py
# now pins the relationship between the two numbers.
LLM_TIMEOUT_SECONDS=45
CRITIC_MAX_REVISIONS=2
SEED_USER_COUNT=5
CHAT_TOKEN_ALLOWANCE=25
MAX_UPLOAD_MB=10
```

One value above is a deliberate override rather than a copy of the default:
`ALLOW_SQLITE_FALLBACK=false`, so the app fails loudly if Postgres is down
instead of silently running on SQLite with no pgvector. Everything else
matches `config.py`.

## Models in `.env` — now supported, with two guards

The original advice was to delete every `MODEL_*` line. That is reversed by
request: the models are listed in `.env.example` and may be set by hand.
Changing a model should not require reading `config.py`.

Two guards make that safe, because the drift was real, not theoretical —
`.env.example` was still advertising `MODEL_WRITING=nvidia:nvidia/
nemotron-3-ultra-550b-a55b` and `MODEL_ATS=groq:openai/gpt-oss-120b` long
after both moved to codecraft, and it had no `EMBEDDING_BACKEND` line at all.

1. **`tests/test_env_example_parity.py`** — every `MODEL_*`,
   `EMBEDDING_*` and `LLM_CACHE_*` line in `.env.example` must equal the
   `config.py` default, and the example may not suggest a retired or
   excluded model. The example can no longer drift.
2. **`llm.RETIRED_MODELS`** — four ids withdrawn after measurement are now
   refused in code and dropped from the chain with the reason logged once,
   instead of being warned about in a comment that `.env` could override.

| retired id | why |
|---|---|
| `nvidia:z-ai/glm-5.3-flash` | burns the full 45s timeout |
| `nvidia:moonshotai/kimi-k2.6` | 404, not callable on this account |
| `nvidia:nvidia/nemotron-3-super-120b-a12b` | HTTP 410, retired by NVIDIA |
| `nvidia:nvidia/nemotron-3.5-lightning-30b-a3b` | 0.33s ping, 45s timeout |

Retirement is kept as a **separate list from the privacy exclusion**
(`EXCLUDED_MODEL_RE`). Exclusion is absolute and about data; retirement is
about measured behaviour and can be reversed if a provider fixes the model.
A test asserts no id is in both, so a privacy ban can never be lifted by a
latency measurement.

## Delete

| line | why |
|---|---|
| `UNOROUTER_API_KEY`, `UNOROUTER_BASE_URL` | legacy provider, retained in code only as a fallback |
| `MODEL_DEFAULT=gemini-…` | **routes everything to the excluded provider.** Now also blocked in code (see below), but still delete it |
| `EMBEDDING_MODEL=gemini-…` | inert — the embedding model is chosen by `EMBEDDING_BACKEND`, now `minilm` (local ONNX, 384-dim) — but misleading |

**`MODEL_*` lines are no longer on this list.** Earlier revisions of this
document told you to delete all eight because each equalled its default. That
advice is withdrawn: you asked to be able to change models from `.env`, and the
duplication is now defended by `tests/test_env_example_parity.py` plus
`llm.RETIRED_MODELS`. Keep all eight.

**`REDIS_URL` is no longer on this list either.** It was listed as "nothing
reads it", which was true before the LLM response cache existed.
`app/ai/cache.py` reads it now and compose ships a Redis. Keep it.

### ✅ SETTLED — 2026-10-11. Writer baseline 7/7.

```
fix rate           7/7
REACHES THE USER   7/7
resisted inventing 3/3
FABRICATIONS       0
answered by        codecraft:claude-sonnet-5     (single model, no blend)
```

First clean sweep this corpus has produced. The final two fixes were both
**prompt guidance that disagreed with the rule the output was scored
against** — `summary.too_short` being told to expand and to stay short at
once, and the revise path being asked to triple a summary's length with no
material to draw on.

**The number is now defended, not just recorded.** `writer_report --llm`
previously graded safety only: INCONCLUSIVE on a collapsed chain, FAIL on an
escaped fabrication, WARN on a contained one, else exit 0. A writer that
declined every case therefore scored `resisted 3/3`, `FABRICATIONS 0` and
**passed** — silence clearing every safety bar while being useless. Same
vacuity hole already closed in `ats_report.py`.

Now `MIN_FIX_RATE` / `MIN_REACHES_USER` = **0.70, meaning ≥ 5/7** — the
granularity is written into the file because with 7 cases only k/7 is
reachable, and a bar set between two reachable points is not the gate you
think it is (a "10% max false-positive" rule on 6 negatives was really a
zero-FP rule earlier in this project).

Floors apply to `--llm` only: the offline rule writer fixes exactly 2/7 by
design and `make writer-report` exists to pin that. The first version of the
gate turned the offline report red, which running it revealed.

Verified by simulating an all-declining writer: `FABRICATIONS 0`,
`resisted 3/3`, and **FAIL**. The first attempt at that check exited 2 via
the INCONCLUSIVE branch instead of the floor — passing for the wrong reason
— so the stub now puts writer and critic on different ids and
`test_it_fails_on_the_floor_not_on_independence` pins it.

**Known and handled: the Groq critic 400s roughly once per run.** Three
modes seen — `output_parse_failed` with prose ("...No new claim. Approved."),
and `json_validate_failed` with an empty body. The first two are recovered by
`CriticVerdict.from_prose`; the third has nothing to salvage and falls to the
next model in the critique chain. No run has been lost to it, but it is the
reason that chain must keep more than one vendor.

### RUN 4 — clean single-model baseline, and the last failure traced

**`codecraft:claude-sonnet-5`, no blend, no INCONCLUSIVE: fix 6/7, reaches
the user 6/7, resisted 3/3, fabrications 0.** That is the settled writer
score. Latencies 6.4–28.7s, all inside the budget — raising
`LLM_TIMEOUT_SECONDS` removed the timeout that split run 3.

**`summary_too_short` flipped from `changed=True` to `changed=False`.** Having
fixed the length contradiction, the model stopped producing a too-short
rewrite and started *declining outright* — the honest answer to an impossible
request, and it exposed the real cause.

`_grounding_context` gives the COMPOSE path the whole resume (roles +
bullets) and the REVISE path five similar bullets "for tone" — or `(none)`
when no vector store is attached, as in the harness. So the model was asked
to turn *"Built platforms for 6 years."* into 30–60 words while forbidden
from inventing and shown no material. **It is the only correct response.**
That is also why `summary_absent` has always passed: composing gets the
substance, revising does not.

Fixed: `needs_substance` extends the compose-scale block to a revise whose
finding cannot be satisfied from the target text alone.

**Deliberately keyed on the FINDING, not the target** — the two summary
findings want opposite things:

| finding | grounding | why |
| --- | --- | --- |
| `summary.too_short` | roles + bullets | cannot reach 30–60 words otherwise |
| `summary.no_metric` | `(none)` | expects a DECLINE; it is the regression test for the 422 bug, and BASE's experience is full of real numbers (22→9 min, 35 percent, 40 services). Handing it those changes what the case measures. |

Pinned by `tests/test_summary_grounding.py`, including a guard that the two
paths actually differ.

**Run 2's separate lesson: `COMPOSER` had an upper bound and no floor.**
"Under 40 words" let nemotron compose summaries that instantly tripped
`summary.too_short` — visible as `summary_absent -> introduced
summary.too_short`. Now "Between 30 and 60 words", with the reason stated in
the prompt. Same bug class as the REVISER contradiction: **prompt guidance
that does not match the rule the output is scored against.**

### RUN 3 — the configuration works, and the last failure was ours

**`REACHES THE USER 6/7`, fabrications 0, resisted inventing 3/3, and no
INCONCLUSIVE warning.** That matches the best score this corpus has ever
produced, and it is the first live run where the writer/critic independence
survived.

**The chain demotion did exactly what it was built for.** One case timed out
on `codecraft:claude-sonnet-5` and fell to
`nvidia:nvidia/nemotron-3-ultra-550b-a55b` — an *independent* model. Under the
old ordering that same timeout would have landed on
`groq:openai/gpt-oss-120b`, the critic's own model, and voided the run. It had
done precisely that twice in a row.

**The one remaining failure, `summary_too_short`, was a contradiction in our
own prompt — not a model defect.**

| source | instruction |
| --- | --- |
| `heuristics.py` finding | "Summary is short (5 words) — **aim for 30–60**" |
| `prompts.REVISER` rule 5 | "Keep the candidate's voice. **Under 30 words**" |

Expand past 30 and stay under 30, in the same request. Both providers failed
it in opposite directions: groq refused outright ("I cannot expand the summary
to 30–60 words without introducing new information") and claude-sonnet-5
obeyed the cap, leaving the finding standing — `changed=True, fixed=False`.

To be exact: the finding clears at **≥ 25 words**, so a 25–29 word answer
would have satisfied both. Nothing told the model that window existed.

Fixed by making the length rule target-dependent — `_length_rule(target_ref)`
gives summaries "30 to 60 words" and bullets "Under 30 words", because the two
are scored against opposite rules and one sentence cannot serve both.
`tests/test_writer_length_rule.py` pins it numerically against the 25-word
threshold, so moving that threshold breaks the test rather than silently
re-opening the contradiction. `prompts.WRITER` keeps its flat 30-word cap on
purpose: that path only ever rewrites bullets.

**Still outstanding: one timeout per run.** `LLM_TIMEOUT_SECONDS` is 45 and
claude-sonnet-5 ran 24.3s / 18.1s / 13.4s on the cases it completed. Raising
it to 90 should remove the blend and give a single-model score.

### FINAL — 2026-10-11. `MODEL_WRITING=codecraft:claude-sonnet-5`

**muse-glimmer is out.** Two live runs, zero usable writer calls:

| run | failure | what came back |
| --- | --- | --- |
| 1 | `unparsable`, all 10 cases | correct rewrites, not JSON |
| 2 | `timeout`, all 10 cases | nothing at all |

Two different failure modes, no successes. It had never once completed the
call the application actually makes, and choosing it was my error twice over:
the "5.2s / 0 defects" came from a **prose-only** probe, and the "7/7" I
quoted beside it was a **different model's** corpus score.

`codecraft:claude-sonnet-5` is the only writer with real corpus evidence —
all ten cases answered, 6/7 fixed, 5/7 reaching the user, 3/3 resisted
inventing, **0 fabrications** — and the fixed probe ranked it fastest of the
33 at 4.2s with a clean `json_schema` re-test.

**Structural fix: the writing chain no longer hands failures to the critic.**
`FALLBACK_CHAIN`'s second entry *is* `groq:openai/gpt-oss-120b`, the critic's
model, so the first writer failure landed there and independence collapsed —
which is exactly what both runs reported. `resolve_chain("writing")` now
demotes it to last:

    codecraft:claude-sonnet-5
      -> nvidia:nvidia/nemotron-3-ultra-550b-a55b
      -> groq:openai/gpt-oss-20b
      -> groq:openai/gpt-oss-120b        (critic's model, last resort)

Demoted rather than dropped: a self-graded rewrite still passes `_hard_check`
and beats refusing the user a rewrite, but every independent option is
exhausted first. Pinned by `test_the_writing_chain_reaches_the_critics_model_last`.

**Two things in run 2 that are working correctly, not bugs.** Groq 400'd with
`failed_generation: "I cannot expand the summary ... without introducing new
information"` — that is a model *correctly declining to fabricate*, and
`from_prose` refuses it as a refusal rather than pasting it into a resume.
And the `REJ:grd -> introduced summary.too_short` rejections are
`_hard_check`'s regression rung catching rewrites that trade one finding for
another.

### PROBE + CORPUS, SECOND ROUND — 2026-10-11

**The fixed probe tested all 33 CodeCraft models: 30 OK, and every one of
them passed the `json_schema` re-test.** Structured output on that provider
is not in question. Fastest clean writers: `claude-sonnet-5` 4.2s,
`claude-opus-4.8` 4.7s, `deepseek-v4-pro-0813` 5.3s, `muse-spark-1.1` 5.3s,
`grok-4.6` 5.6s. Three failures: `gpt-5.5-pro`, `gpt-5.6-sol` (immediate
errors) and `gpt-5.6-luna` (passed prose, **failed the schema re-test** —
exactly what that re-test was added to catch).

**`nvidia:meta/muse-glimmer-30b` cannot emit JSON at all.** It failed all ten
writer-corpus cases with `unparsable`, every payload a *correct* rewrite:

    input_value='Reduced CI pipeline runtime from 22 minutes to 9 minutes.'

**This invalidates the revert recorded below, and the reasoning behind it was
my error.** muse-glimmer was chosen off a `models-gen-all nvidia` run that
predated `_probe_structured()` — a **prose-only** probe. Its "5.2s, 0
defects" was never a measurement of the production path, and the "7/7, 6/7"
I quoted alongside it was the corpus *baseline from a different model*. Two
unrelated numbers presented as one model's record. This is the third time
this project has been bitten by trusting a weaker probe than the real call.

**Consequence beyond the writer:** with no `from_prose()` on `RevisionDraft`,
every failure fell through to `groq:openai/gpt-oss-120b` — the critic's own
model. The writer was graded by itself and the report said INCONCLUSIVE.

**Fix: `RevisionDraft.from_prose()` now exists**, so a prose-only model is
recovered rather than lost. It fails closed — refusals, preambles
(`"Here is a better bullet: ..."`), meta-commentary (`"...violates Rule 1"`),
questions, JSON fragments and multi-paragraph replies are all refused. A
pre-existing tripwire in `test_llm_transport.py` demanded exactly that and
caught the first version accepting a preamble. Salvaged drafts still pass
`_hard_check` and still face the critic: this recovers the *text*, never the
approval.

**`MODEL_WRITING` is left at `nvidia:meta/muse-glimmer-30b` pending one
measurement.** With salvage in place its rewrites should now land, and NVIDIA
tokens do not count against the CodeCraft allowance — which is what makes the
budget work. **Re-run `make writer-report-llm`.** If "answered by" still shows
groq, salvage is not catching it: switch to `codecraft:claude-sonnet-5`
(measured 6/7 fix, 5/7 reaches user, 0 fabrications) and accept the token
cost, or try `codecraft:deepseek-v4-pro-0813` / `grok-4.6`.

**Excluded from consideration regardless of score:** `gemini-3.1-pro`,
`gemini-3.6-flash`, `gemini-3.7-flash`, `gemma-2-2b` — the Google exclusion
follows the data, not the brand, and applies through a reseller.

### MEASURED OUTCOME — 2026-10-11 (supersedes the estimate below)

The provider was wired, keyed, and run live. **`MODEL_WRITING` is reverted to
`nvidia:meta/muse-glimmer-30b`; `MODEL_ATS` stays on codecraft.**

**`make writer-report-llm`, real corpus, codecraft answered all ten cases —
so `json_schema` works there. That question is settled.** But it lost:

| | muse-glimmer-30b | claude-sonnet-5 |
| --- | --- | --- |
| fix rate | **7/7** | 6/7 |
| reaches the user | **6/7** | 5/7 |
| resisted inventing | 3/3 | 3/3 |
| fabrications | 0 | 0 |
| latency | **5.2s** | 10–44s (44.2s inside a 45s budget) |
| tokens/call | **~900** | ~5,600–7,250 |

**The token column is a correction to my own arithmetic, not a new
measurement of the model.** The estimate below measured *input* precisely with
`tiktoken` and then **guessed output at 400 tokens**. The real figure is 6–8×
that, because this model emits a long reasoning trace that bills as output —
visible in the 44-second latencies. The 79K reported for one evaluation
session is what exposed it.

Rebuilt against the real number, at 40 analyses/month:

| rewrites per analysis | tokens | of 1M |
| --- | --- | --- |
| 2, 20% retried | 770K | 77% |
| 4, 20% retried | 1.31M | **131%** |
| 6, 30% retried | 1.97M | **197%** |

Break-even is **4 writer calls per analysis**. So "25%, 4× headroom" was wrong
for this model; it does not fit the free plan at all. On muse-glimmer the
original ~25% stands.

**`MODEL_ATS` passed cleanly and stays:** 8/8 correct, 0 wrong, **0
fabricated** against a kill criterion of 0, 100% correct rate. One wobble —
`AWS` expanded on only 1 of 3 runs — which is a missed expansion, not a wrong
one, and silence is an acceptable outcome by design. ATS is ~6.5K tokens a
month: noise.

**Do not read this as "codecraft is bad."** One model is not a catalogue, and
`models-gen-all codecraft` **never tested any of the 33** — it selected zero
models and printed "No model of this provider can generate inside the budget",
which looked like a verdict and was a bug in the probe (now fixed, pinned by
`tests/test_probe_model_selection.py`). Re-run it before concluding anything;
a faster model there could plausibly beat muse-glimmer. Pick on the report,
never on the name.

### Is the 1M tokens/month free plan enough?

**For normal use yes, for heavy use no**, and the crossover sits inside the
range this project plausibly operates in. Measured on the real test resume
with `tiktoken`, not estimated:

| call | when | tokens |
| --- | --- | --- |
| writer batch | once per Analyze that has a JD | **1,631** |
| writer rewrite | per rewrite click, **×2 if the critic rejects the first draft** | **893** |
| ats review | gated — only fires when a bare acronym is present | 269 |

(Each writer figure includes the 279-token RAG block of the candidate's own
retrieved bullets, which an earlier estimate of this missed entirely.)

| scenario | analyses/mo | rewrites | tokens | of 1M |
| --- | --- | --- | --- | --- |
| 1 user trying it out | 8 | 28 | 41K | 4% |
| 5 users, job hunting | 40 | 192 | 247K | 25% |
| 5 users, weekly tailoring | 80 | 384 | 495K | 49% |
| 5 users, daily tailoring | 150 | 975 | 1.16M | **116%** |
| 5 users, heavy + retries | 150 | 1800 | 1.89M | **189%** |

**This project's actual profile: 1 user, 40 analyses/month → ~25% of the
allowance, 4x headroom.** Confirmed as the operating assumption. Even the
pessimistic case — 10 rewrites per analysis with *every single one* retried by
the critic — lands at 79%. To exhaust 1M you would need 1,035 rewrite calls,
i.e. 26 per analysis; `MAX_SUGGESTIONS` is 6, so that is not reachable by
clicking.

**Measurement is therefore nearly free, and should be used.** A
`writer-report-llm` run is 10 cases ≈ 8,930 tokens = **0.9%** of the month;
`--runs 3` is 2.7%; `models-gen-all codecraft` is 5.9%. All three together are
~11%, which fits twice over inside the 25% already budgeted. There is no cost
argument for guessing instead of measuring here.

**A corollary worth stating, because it inverts the usual instinct:** the free
plan meters **tokens, not money**. 1M tokens is 1M tokens whether they are
spent on the $0.12 model or the $8.30 one, and the listed per-token prices only
begin to matter *after* the allowance is gone — which at 25% usage will not
happen. So "pick a cheap model to make the free tier last" is backwards. Pick
the one that scores best on `writer-report-llm` and ignore its price tier
entirely. (Do not infer capability from the *name*, either: several ids in this
catalogue are not products the vendor they cite actually ships.)

**Two things fall out of this.**

**`MODEL_ATS` is free in practice.** Even firing on every single analysis it
is 4% of the month, and it is gated — on the real test resume `bare_acronyms`
returns `[]`, so it never fires at all. The slot with no privacy cost also has
no capacity cost.

**`MODEL_WRITING` is the entire budget.** The same slot that carries the
resume PII is the one that consumes the allowance, and the rewrite retry is
the swing factor: a run where the critic rejects every first draft doubles the
rewrite cost. Rough rule — **you are fine below roughly 100 analyses a month,
and over budget somewhere past 130.**

Rate limits are a non-issue: 60 req/min and 100K tokens/min work out to ~61
batch calls or ~112 rewrites *per minute*, which five people clicking buttons
cannot reach.

**What happens when the allowance runs out:** CodeCraft returns HTTP 402.
`llm.classify` buckets that as `quota_exhausted` (not `rate_limited`, which
would make `structured()` sleep pointlessly, and not a bare `error`, which
would make an exhausted budget look like a bad model in a report). The chain
falls through to Groq and then NVIDIA, so the app keeps working — and the
resume data then goes to the named vendors anyway.

### Before you set `CODECRAFT_API_KEY`

CodeCraft is fast and genuinely free, and it fixes the Groq rate limit that
interrupts `ats-report-llm`. It also **names no upstream provider anywhere on
its site**, and its catalogue contradicts itself — `muse-spark-1.1` is
described as a Meta model while carrying Zhipu's logo, and ids like
`gpt-5.6-terra` and `claude-mythos-preview` are not products those vendors
ship. You therefore cannot establish whose servers a payload reaches, in which
country, or under what retention.

That is a genuine non-issue for **`MODEL_ATS`**: the whole payload is
`Skills written only as an acronym:\nPMP, CKA`, and
`tests/test_ats_payload_privacy.py` fails the build if that ever changes.

It is a real decision for **`MODEL_WRITING`**, which sends the resume itself —
name, phone, address, employers, dates. That is the same category of data the
Google exclusion at the top of this file was about. The project owner chose it
knowingly; if you are a different reader, choose knowingly too.

**`MODEL_CRITIQUE` must not point here while the writer does.** The critic's
job is catching the writer's fabrications, and a test enforces that the two sit
on different providers. With an undisclosed upstream, two ids may be the same
model wearing two names, so the independence could not even be verified.

## The seven agents and why each sits where it does

| task | model | reasoning |
|---|---|---|
| `writing` | **nvidia** nemotron-3-ultra-550b | must differ in vendor from the critic; unverified for real generation |
| `critique` | groq gpt-oss-120b | grades the writer; the independence constraint lives here |
| `chat` | groq gpt-oss-120b | user-facing prose |
| `extraction` | groq gpt-oss-120b | structured output from messy resumes |
| `jd_analysis` | groq gpt-oss-120b | **not** nemotron-lightning-30b: pings in 0.33s, times out at 45s |
| `scoring` | groq gpt-oss-**20b** | prose about a number that is already decided — low stakes |
| `ats` | groq gpt-oss-120b | **new.** Hardest judgement in the product |

### Why `ats` is not `scoring`

It was briefly wired to `scoring`, which silently put the **smallest model in the
catalogue** on the one task where being wrong costs the most. The ATS agent states
facts about the world — `CKA` is *Certified Kubernetes Administrator* — and a wrong
one puts a credential on the resume that the candidate does not hold.

The slot used to carry a harder task too (telling "My Journey", a hidden work
history, from "Leadership & Achievements", genuinely custom). That half was
**deleted on 2026-10-11** after failing a pre-registered false-positive gate on a
real resume; see `PLAN-ATS-COMPAT.md`. The 120b stays regardless: recall of
real-world credential names is exactly what the larger model is better at.

It has its own key, so you can move it without disturbing scoring. Measure
before trusting it: `make ats-report-llm`.

---

# The block you pasted, reviewed line by line

Verified mechanically, not by eye: both `Settings()` objects were constructed
and diffed.

```
11 keys set; 1 actually changes anything
```

**Ten of the eleven lines are exact copies of the `config.py` default.** They
are not wrong — they are inert. Every one of them is also a future trap of
exactly the kind you already hit: your last `.env` pinned
`MODEL_DEFAULT=gemini-3.5-flash-lite:free`, the default moved on, and the file
silently kept overriding it with the excluded provider. A line that repeats a
default cannot help you and can only drift.

| key | your value | verdict |
|---|---|---|
| `MODEL_DEFAULT` | `groq:openai/gpt-oss-120b` | = default · **delete** |
| `MODEL_WRITING` | `codecraft:claude-sonnet-5` | = default · **delete** |
| `MODEL_CHAT` | `groq:openai/gpt-oss-120b` | = default · **delete** |
| `MODEL_CRITIQUE` | `groq:openai/gpt-oss-120b` | = default · **delete** (also: keeps writer≠critic by provider — do not change it to codecraft) |
| `MODEL_EXTRACTION` | `groq:openai/gpt-oss-120b` | = default · **delete** |
| `MODEL_JD_ANALYSIS` | `nvidia:z-ai/glm-5.3-flash` | ⛔ **the one line to drop** — see below |
| `MODEL_SCORING` | `groq:openai/gpt-oss-20b` | = default · **delete** |
| `MODEL_ATS` | `codecraft:deepseek-v4-pro-0813` | = default · **delete** |
| `EMBEDDING_MODEL` | `nvidia:nvidia/nemotron-3-embed-1b` | = default, and **unused** while `EMBEDDINGS_REMOTE=false` · delete |
| `EMBEDDINGS_REMOTE` | `false` | = default · correct, keep it false (see below) |
| `LLM_TIMEOUT_SECONDS` | `45` | = default · **delete** |

## ⛔ `MODEL_JD_ANALYSIS=nvidia:z-ai/glm-5.3-flash`

This is the only line with an effect, and it is the one model in your list the
codebase has already removed **by measurement**. The comment is still sitting
in `app/ai/llm.py` where it was deleted from the fallback chain:

> `nvidia:z-ai/glm-5.3-flash` sat here and was removed: `make models` showed it
> **burning the full 45s timeout rather than answering**.

It is also the third NVIDIA model in a row to ping fast and then fail real
generation — `nemotron-3.5-lightning-30b-a3b` (0.33s ping, 45s timeout),
`nemotron-3-super-120b-a12b` (retired mid-flight, HTTP 410), and
`nemotron-3-ultra-550b-a55b` (measured at 45,187 ms on a real scoring call).
A ping does not predict generation, and this account has four data points
saying so.

JD analysis is **on the critical path of tailoring** — you paste a job
description and wait for it. Putting a model there that is recorded as timing
out means a 45-second stall before the fallback chain even starts.

**Keep the default:** `groq:openai/gpt-oss-120b`.

If you want NVIDIA in the mix for cost reasons, the honest way is to measure
first: `make models-gen-all PROVIDER=nvidia`, and promote only an id that is
**observed to generate**. That tool exists because of this exact mistake.

## `EMBEDDINGS_REMOTE=false` is right — leave it

Not only for cost. The pgvector column is `Vector(384)`. Hosted embeddings are
not 384-dimensional, so a **successful** remote call is the one that breaks the
insert, and mixing two embedding spaces in one index makes cosine similarity
meaningless. `EMBEDDING_MODEL` is kept valid only so flipping the flag does not
404.

## `LLM_TIMEOUT_SECONDS=45`

Matches the default, so delete the line — but know what it buys you. 45s is
tuned for the Groq-first chain. The writer is `codecraft:claude-sonnet-5` and a
quantify rewrite runs writer → critic → up to 2 revisions, so the user-visible
worst case is several of these in series. If rewrites start failing with
`timeout` rather than returning text, this is the number to raise — and the
place to prove it is a real generation run, not a ping.

## What your `.env` should actually contain

See **Keep** at the top of this document — it now lists the complete file,
including all eight `MODEL_*` lines, the embedding keys and `REDIS_URL`.

An earlier revision ended here with a short file and the claim *"No `MODEL_*`
lines at all"*. That is withdrawn. The reasoning behind it was sound — a second
copy of a default is a second thing that can drift — but its premise was that
nothing defended the copy. Two guards now do, and you asked to be able to swap
models without editing source. The duplication is deliberate and tested.

---

# New keys: local embeddings and the response cache

Both default correctly. Add a line only to change behaviour.

| key | default | notes |
|---|---|---|
| `EMBEDDING_BACKEND` | `minilm` | `minilm` (local, semantic, 384-dim) · `hashed` (no network, no semantics) · `remote` |
| `LLM_CACHE_ENABLED` | `false` | set `true` once Redis is up |

**One-time setup for `minilm`:**

```powershell
conda env update -f backend/environment.yaml --prune   # adds fastembed
.\make.ps1 fetch-models                                # ~90 MB, once
python -m app.cli db reset                             # re-embed
```

The reset is **not optional**. Hashed and MiniLM vectors are both 384 wide,
so nothing detects a mix by shape — and mixing two embedding spaces in one
index makes cosine similarity meaningless. Rows now record their backend and
a search excludes foreign ones with a warning naming the fix, but the stored
vectors are still stale until you re-embed.

**To turn the cache on**, once `docker compose up -d redis` is running:

```ini
LLM_CACHE_ENABLED=true
```

`REDIS_URL` already defaults to `redis://localhost:6379/0`. The app runs
identically with Redis stopped — every cache error degrades to a miss.
