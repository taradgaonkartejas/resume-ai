import re
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# app/config.py -> app/ -> backend/ -> repo root
BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = Path(__file__).resolve().parents[2]

# Two anchors, deliberately:
#
#   BACKEND_DIR  where .env lives. The backend owns its own configuration, so
#                the frontend can own frontend/.env.local without the two
#                fighting over one file at the root.
#   ROOT_DIR     where runtime artefacts live (data/). It stays at the repo
#                root because that is where the existing exports, the SQLite
#                fallback database and the LangGraph checkpoints already are.
#                Repointing it at backend/ would silently strand all of them.
#
# Both are absolute and derived from __file__, never from the cwd: env_file is
# resolved relative to the working directory, so a relative ".env" would load
# when uvicorn starts in backend/ and silently not load anywhere else — every
# setting falling back to its default with no error.


# Values that are syntactically present but cannot be a credential. No real
# key starts with "#", and none is a bracketed/angled placeholder.
_PLACEHOLDER_RE = re.compile(r"^(#|<|\[|your[-_ ]|xxx+$|changeme$|todo$)", re.I)


def is_placeholder(value: str) -> bool:
    """True when `value` is blank or obviously not a real API key.

    An unset key is free -- the provider is dropped without a network call. A
    garbage key costs a round trip and returns 401, which reads like an
    entitlement problem rather than the config typo it is.
    """
    text = str(value or "").strip()
    return not text or bool(_PLACEHOLDER_RE.match(text))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # LLM — optional; the app degrades gracefully without a key.
    #
    # One key per provider. A chain spans vendors, so holding only one of
    # these is fine: resolve_chain() skips the models it has no key for.
    # Base URLs live in llm.PROVIDERS, not here -- they are facts about the
    # vendor, not things an operator should be editing.
    nvidia_api_key: str = ""      # build.nvidia.com  — no card, ~40 req/min
    groq_api_key: str = ""        # console.groq.com  — no card, 30 req/min
    codecraft_api_key: str = ""   # codecraftapi.com — no card, 60 req/min
    unorouter_api_key: str = ""   # legacy; 1 req/min/model, hence the move

    # Per-task routing, qualified as "provider:model".
    #
    # writing/chat go to a chat-tuned model because the user reads that prose.
    # critique goes to a DIFFERENT VENDOR on purpose: the critic exists to
    # catch what the writer got wrong, and a model does not audit itself well.
    #
    # NVIDIA ids below were read from the live catalogue (82 models) on
    # 2026-09-27, not guessed -- an earlier draft of this block invented
    # "kimi-k2.5", "qwen/qwen3.5" and "nemotron-3-super-120b", none of which
    # exist. Free catalogues churn, so re-check with `make models`, which
    # flags any id that is not in the provider's catalogue.
    # Every id below carries a "Free Endpoint" badge on build.nvidia.com or is
    # on Groq's free list. kimi-k2.6 and glm-5.3-flash used to sit here and are
    # NOT free endpoints -- they 404 and time out respectively on this plan.
    model_default: str = "groq:openai/gpt-oss-120b"
    # nemotron-3-super was retired (410) on 2026-10-03 and took five task
    # slots with it. The writer must stay on a DIFFERENT VENDOR from the
    # critic (groq) -- a writer graded by its own family is not an
    # independent measurement, and there is a test that enforces it.
    #
    # 2026-10-11: was nemotron-3-ultra-550b-a55b, replaced after it timed out
    # at 45,187 ms on a real scoring call in the user's own logs. That id had
    # been marked UNVERIFIED FOR GENERATION in this very comment; the
    # verification arrived as a production failure, which is the expensive
    # way to get it.
    #
    # muse-glimmer-30b was measured by `make models-gen-all nvidia`:
    # 5.2s, 21 words, 0 instruction defects. Two faster models were passed
    # over deliberately:
    #
    #   openai/gpt-oss-20b (1.5s)  -- same `openai/gpt-oss` family as the
    #       critic, so the writer would be judged by a sibling. Independence
    #       of writer and critic is enforced by a test; see below.
    #   google/diffusiongemma-26b-a4b-it (1.1s) -- Google-authored. No data
    #       reaches Google here (NVIDIA hosts the weights), but the user's
    #       exclusion of Google is firmly held and not worth 4 seconds.
    #
    # STILL ONLY PARTLY VERIFIED. `models-gen-all` generates plain prose,
    # while every real call goes through `llm.structured()` with a
    # json_schema -- and `RevisionDraft` has no `from_prose()`, so it has no
    # salvage rung if the model cannot do schemas. Confirm with
    # `make writer-report-llm` before trusting this for real work.
    # 2026-10-11, FOURTH change. muse-glimmer is OUT on two live runs that
    # produced ZERO usable writer calls between them:
    #
    #   run 1  every case `unparsable` -- it cannot emit JSON. The payloads
    #          were correct rewrites, which is why RevisionDraft.from_prose()
    #          was added, to recover that shape in general.
    #   run 2  every case `timeout` -- it did not answer at all.
    #
    # Two different failure modes, zero successes. It was only ever chosen
    # off a PROSE-ONLY probe that predated _probe_structured(), so it had
    # never once completed the call the application actually makes. Picking
    # it was my error, twice over: the "5.2s / 0 defects" was a prose probe,
    # and the "7/7" quoted beside it was a different model's corpus score.
    #
    # codecraft:claude-sonnet-5 is the only writer with real corpus evidence:
    # it answered all ten cases -- 6/7 fixed, 5/7 reaching the user, 3/3
    # resisted inventing, 0 fabrications. The fixed probe also ranked it
    # fastest of the 33 (4.2s) with a clean json_schema re-test.
    #
    # Known cost, accepted deliberately: in the corpus run it averaged ~6k
    # tokens per call against codecraft's 1M/month, so heavy use will exhaust
    # the allowance. That now degrades honestly instead of looking like a
    # broken model -- 402 classifies as `quota_exhausted` and the chain falls
    # through to groq/nvidia.
    model_writing: str = "codecraft:claude-sonnet-5"
    model_chat: str = "groq:openai/gpt-oss-120b"
    model_critique: str = "groq:openai/gpt-oss-120b"
    model_extraction: str = "groq:openai/gpt-oss-120b"
    # NOT nemotron-3.5-lightning-30b-a3b. It pings in 0.33s -- the fastest
    # model in the catalogue -- and then times out at 45s on a real 40-word
    # generation. Chosen on ping latency one turn after building the tool
    # whose entire purpose is that a ping does not predict generation.
    model_jd_analysis: str = "groq:openai/gpt-oss-120b"
    model_scoring: str = "groq:openai/gpt-oss-20b"
    # The ATS agent is NOT scoring work and must not inherit the 20b model.
    # Scoring writes prose about a number that is already decided -- low
    # stakes, so the small model is right. The ATS agent states FACTS about
    # the world ("CKA" = Certified Kubernetes Administrator), and a wrong
    # one puts a credential on the resume that the user does not hold.
    #
    # This slot used to justify itself with a harder task -- deciding whether
    # "My Journey" is a hidden work history or "Leadership & Achievements" is
    # genuinely custom. That half was deleted on 2026-10-11 after failing a
    # pre-registered false-positive gate. The 120b stays anyway: the
    # remaining task is recall of real-world credential names, which is
    # exactly what the larger model is better at.
    # 2026-10-11 -> codecraft. This is the slot where the move is clearly
    # right: after the relabel half was deleted, the entire ATS payload is
    #
    #     "Skills written only as an acronym:\nPMP, CKA"
    #
    # -- no name, no employer, no dates, nothing personal. The privacy
    # objection that applies to writing simply does not apply here, and
    # codecraft's 60 req/min fixes the Groq 429 that interrupted the
    # ats-report run. Groq remains in the chain underneath.
    model_ats: str = "codecraft:deepseek-v4-pro-0813"

    # Unused while embeddings_remote is False (see below) but kept valid so
    # a flip of that flag does not 404.
    embedding_model: str = "nvidia:nvidia/nemotron-3-embed-1b"
    # When true, a model that is not free on the plan we are on is refused
    # before a request is ever sent. A fallback chain is exactly the code path
    # that quietly reaches for a paid model at 3am. Opt out deliberately.
    free_models_only: bool = True
    # Embeddings run locally by default, and not only to save money.
    #   1. gemini-embedding-2:free reports 24.3% success, so ~3 in 4 calls
    #      already fell through to the local embedder.
    #   2. The pgvector column is Vector(embedding_dim) = 384. Hosted Gemini
    #      embeddings are not 384-dimensional, so a *successful* remote call is
    #      the case that breaks the insert.
    #   3. Mixing two embedding spaces in one index makes cosine similarity
    #      meaningless -- worse than either space used consistently.
    # For a 5-user local deployment over a small corpus, one consistent space
    # beats a 24% chance of a better one.
    embeddings_remote: bool = False
    # Which embedder runs: "minilm" (local all-MiniLM-L6-v2 via fastembed,
    # 384-dim, semantic), "hashed" (deterministic bag-of-words, no network,
    # no semantics -- used by the test suite), or "remote".
    #
    # 384 is not a coincidence: it is MiniLM's output width and was already
    # the pgvector column width, so the semantic upgrade needs no migration.
    #
    # Switching backends makes every stored vector meaningless -- two spaces
    # in one index make cosine similarity nonsense -- so a change here
    # requires `python -m app.cli db reset`. Rows record the backend that
    # produced them and a search refuses to mix them.
    embedding_backend: str = "minilm"
    llm_timeout_seconds: int = 45
    # Redis response cache for deterministic tasks (extraction, jd_analysis,
    # scoring, ats). NEVER the writer: it runs at temperature 0.4 so that the
    # "try again" on a critic rejection can produce a different draft.
    # Off by default -- a cache that appears without being asked for is a
    # cache nobody knows to invalidate. The app runs identically with Redis
    # stopped; failures degrade to a miss.
    llm_cache_enabled: bool = False
    critic_max_revisions: int = 2

    # Data
    database_url: str = "postgresql+psycopg2://admin:Admin123@localhost:5433/resumeai"
    allow_sqlite_fallback: bool = True
    redis_url: str = "redis://localhost:6379/0"

    # Object storage
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minio"
    s3_secret_key: str = "minio123"
    s3_bucket_uploads: str = "resumeai-uploads"
    s3_bucket_exports: str = "resumeai-exports"
    allow_disk_fallback: bool = True

    # App
    seed_user_count: int = 5
    chat_token_allowance: int = 25
    max_upload_mb: int = 10
    embedding_dim: int = 384

    @property
    def llm_configured(self) -> bool:
        """Any provider key at all. See llm.is_configured() for the live check.

        Uses is_placeholder() rather than truthiness so a leftover comment or
        `<your-key>` does not read as a configured provider here while
        llm.Provider.configured correctly rejects it.
        """
        return any(
            not is_placeholder(k)
            for k in (
                self.nvidia_api_key,
                self.groq_api_key,
                self.codecraft_api_key,
                self.unorouter_api_key,
            )
        )


settings = Settings()
