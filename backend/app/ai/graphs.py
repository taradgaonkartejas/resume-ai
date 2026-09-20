"""LangGraph graphs: analysis and tailoring.

Node bodies stay thin — state in, one agent call, state out — so the topology
is testable without a network. All orchestration lives here; all prompting
lives in agents.py.

The tailor graph pauses at AwaitUserReview via a durable interrupt. The pause
survives a process restart because the checkpointer persists to SQLite (or
Postgres), which is what makes Accept/Reject/Edit work across HTTP requests.
"""

from __future__ import annotations

import atexit
import logging
import operator
import sqlite3
import threading
import uuid
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.engine import make_url

from app.ai import agents
from app.ai.vectorstore import VectorStore
from app.config import settings
from app.db import DATA_DIR, DB_BACKEND
from app.services.heuristics import infer_role_tags, match_keywords, score_resume

logger = logging.getLogger(__name__)

_CHECKPOINT_LOCK = threading.Lock()
_CHECKPOINTER = None
_CONN = None


def get_checkpointer():
    """Process-wide durable checkpointer.

    Uses Postgres when the app is on Postgres, so a paused tailoring run lives
    in the same database as everything else — one backup, one restore, and the
    pause survives across workers rather than only across threads of one
    process. Falls back to SQLite otherwise.
    """
    global _CHECKPOINTER, _CONN
    with _CHECKPOINT_LOCK:
        if _CHECKPOINTER is not None:
            return _CHECKPOINTER

        if DB_BACKEND == "postgresql":
            try:
                _CHECKPOINTER, _CONN = _postgres_checkpointer()
                return _CHECKPOINTER
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Postgres checkpointer unavailable (%s: %s) — falling back "
                    "to the SQLite checkpoint store.",
                    type(exc).__name__,
                    exc,
                )

        from langgraph.checkpoint.sqlite import SqliteSaver

        # check_same_thread=False: uvicorn serves from a threadpool, so the
        # graph is resumed by a different thread than the one that paused it.
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "agent_checkpoints.db"
        _CONN = sqlite3.connect(str(path), check_same_thread=False)
        _CHECKPOINTER = SqliteSaver(_CONN)
        return _CHECKPOINTER


def _postgres_checkpointer():
    """Build a PostgresSaver on its own psycopg3 pool.

    LangGraph needs psycopg3 while SQLAlchemy here uses psycopg2, so the DSN is
    translated and the pool is kept separate from the ORM's engine.
    """
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg_pool import ConnectionPool

    dsn = make_url(settings.database_url).set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    pool = ConnectionPool(
        conninfo=dsn,
        min_size=1,
        max_size=5,
        open=True,
        # autocommit is required: the saver issues its own transactions.
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    saver = PostgresSaver(pool)
    saver.setup()  # idempotent: creates the checkpoint tables if missing

    # Without this the pool's worker threads outlive the interpreter and
    # psycopg logs "couldn't stop thread" on every exit.
    atexit.register(_close_pool, pool)
    return saver, pool


def _close_pool(pool) -> None:
    try:
        pool.close()
    except Exception:  # noqa: BLE001
        pass


def reset_checkpointer() -> None:
    """Test hook — drops the cached connection."""
    global _CHECKPOINTER, _CONN
    with _CHECKPOINT_LOCK:
        if _CONN is not None:
            try:
                _CONN.close()  # works for both sqlite3.Connection and ConnectionPool
            except Exception:  # noqa: BLE001
                pass
        _CHECKPOINTER = None
        _CONN = None


# ============================================================== analysis graph
class AnalysisState(TypedDict, total=False):
    structured_data: dict
    ats_context: list
    rule_result: dict
    role_tags: list
    commentary: dict
    trace: Annotated[list, operator.add]


def _make_analysis_graph(store: VectorStore | None):
    def retrieve_ats(state: AnalysisState) -> AnalysisState:
        hits: list = []
        if store is not None:
            try:
                hits = store.search("resume quality ats rules", corpus="ats_rules", k=5)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ATS retrieval failed: %s", exc)
        return {
            "ats_context": [h.content for h in hits],
            "trace": [{"node": "RetrieveATS", "hits": len(hits)}],
        }

    def rule_engine(state: AnalysisState) -> AnalysisState:
        data = state.get("structured_data") or {}
        result = score_resume(data)
        return {
            "rule_result": result,
            "role_tags": infer_role_tags(data),
            "trace": [
                {
                    "node": "RuleEngine",
                    "overall_score": result["overall_score"],
                    "deterministic": True,
                }
            ],
        }

    def scoring_agent(state: AnalysisState) -> AnalysisState:
        outcome = agents.score_commentary(
            state.get("structured_data") or {}, state["rule_result"], store
        )
        return {
            "commentary": outcome.value,
            "trace": [
                {
                    "node": "ScoringAgent",
                    "agent": outcome.agent,
                    "model": outcome.model,
                    "used_llm": outcome.used_llm,
                    "latency_ms": outcome.latency_ms,
                }
            ],
        }

    builder = StateGraph(AnalysisState)
    builder.add_node("RetrieveATS", retrieve_ats)
    builder.add_node("RuleEngine", rule_engine)
    builder.add_node("ScoringAgent", scoring_agent)
    builder.add_edge(START, "RetrieveATS")
    builder.add_edge("RetrieveATS", "RuleEngine")
    builder.add_edge("RuleEngine", "ScoringAgent")
    builder.add_edge("ScoringAgent", END)
    return builder.compile()


def run_analysis(structured_data: dict, store: VectorStore | None = None) -> dict:
    """Analysis is synchronous and stateless — no checkpointer needed."""
    graph = _make_analysis_graph(store)
    return graph.invoke({"structured_data": structured_data, "trace": []})


# =============================================================== tailor graph
class TailorState(TypedDict, total=False):
    resume_id: str
    user_id: str
    structured_data: dict
    jd_text: str
    keywords: list
    match: dict
    drafts: list
    approved: list
    rejected: list
    decision: str
    revision_round: int
    trace: Annotated[list, operator.add]


def _make_tailor_graph(store: VectorStore | None, max_revisions: int):
    def parse_jd(state: TailorState) -> TailorState:
        return {"trace": [{"node": "ParseJD", "chars": len(state.get("jd_text", ""))}]}

    def extract_keywords_node(state: TailorState) -> TailorState:
        outcome = agents.analyse_jd(state.get("jd_text", ""), store)
        payload = outcome.value
        return {
            "keywords": payload["keywords"],
            "trace": [
                {
                    "node": "ExtractKeywords",
                    "agent": outcome.agent,
                    "model": outcome.model,
                    "used_llm": outcome.used_llm,
                    "count": len(payload["keywords"]),
                }
            ],
        }

    def retrieve_context(state: TailorState) -> TailorState:
        data = state.get("structured_data") or {}
        scores = match_keywords(data, list(state.get("keywords") or []))
        return {
            "match": scores,
            "trace": [
                {
                    "node": "RetrieveResumeContext",
                    "matched": len(scores["matched_keywords"]),
                    "gaps": len(scores["gap_keywords"]),
                }
            ],
        }

    def generate(state: TailorState) -> TailorState:
        data = state.get("structured_data") or {}
        gaps = list(state.get("match", {}).get("gap_keywords", []))
        user_id = state.get("user_id")
        resume_id = state.get("resume_id")
        outcome = agents.write_suggestions(
            data,
            gaps,
            user_id=uuid.UUID(user_id) if user_id else None,
            resume_id=uuid.UUID(resume_id) if resume_id else None,
            store=store,
        )
        return {
            "drafts": outcome.value,
            "trace": [
                {
                    "node": "GenerateSuggestions",
                    "agent": outcome.agent,
                    "model": outcome.model,
                    "used_llm": outcome.used_llm,
                    "drafts": len(outcome.value),
                    "round": state.get("revision_round", 0),
                }
            ],
        }

    def critic_review(state: TailorState) -> TailorState:
        data = state.get("structured_data") or {}
        approved: list = []
        rejected: list = []
        for draft in state.get("drafts") or []:
            outcome = agents.review_draft(draft, data)
            verdict = outcome.value
            if verdict.approved:
                approved.append({**draft, "grounded": True, "critic_notes": ""})
            else:
                rejected.append(
                    {
                        **draft,
                        "grounded": False,
                        "critic_notes": verdict.notes,
                        "severity": verdict.severity,
                    }
                )
        return {
            "approved": approved,
            "rejected": rejected,
            "revision_round": state.get("revision_round", 0) + 1,
            "trace": [
                {
                    "node": "CriticReview",
                    "approved": len(approved),
                    "rejected": len(rejected),
                    "round": state.get("revision_round", 0) + 1,
                }
            ],
        }

    def route_after_critic(state: TailorState) -> str:
        """Revise only if everything was rejected and we have budget left."""
        approved = state.get("approved") or []
        rejected = state.get("rejected") or []
        rounds = state.get("revision_round", 0)
        if not approved and rejected and rounds < max_revisions:
            return "GenerateSuggestions"
        return "AwaitUserReview"

    def await_user_review(state: TailorState) -> TailorState:
        """Durable pause. Resumed by a later HTTP request, possibly another process."""
        decision = interrupt(
            {
                "suggestions": state.get("approved") or [],
                "match": state.get("match") or {},
            }
        )
        return {
            "decision": str(decision),
            "trace": [{"node": "AwaitUserReview", "decision": str(decision)}],
        }

    def recompute(state: TailorState) -> TailorState:
        data = state.get("structured_data") or {}
        scores = match_keywords(data, list(state.get("keywords") or []))
        return {
            "match": scores,
            "trace": [
                {"node": "RecomputeMatchScore", "match_percent": scores["match_percent"]}
            ],
        }

    builder = StateGraph(TailorState)
    builder.add_node("ParseJD", parse_jd)
    builder.add_node("ExtractKeywords", extract_keywords_node)
    builder.add_node("RetrieveResumeContext", retrieve_context)
    builder.add_node("GenerateSuggestions", generate)
    builder.add_node("CriticReview", critic_review)
    builder.add_node("AwaitUserReview", await_user_review)
    builder.add_node("RecomputeMatchScore", recompute)

    builder.add_edge(START, "ParseJD")
    builder.add_edge("ParseJD", "ExtractKeywords")
    builder.add_edge("ExtractKeywords", "RetrieveResumeContext")
    builder.add_edge("RetrieveResumeContext", "GenerateSuggestions")
    builder.add_edge("GenerateSuggestions", "CriticReview")
    builder.add_conditional_edges(
        "CriticReview",
        route_after_critic,
        {
            "GenerateSuggestions": "GenerateSuggestions",
            "AwaitUserReview": "AwaitUserReview",
        },
    )
    builder.add_edge("AwaitUserReview", "RecomputeMatchScore")
    builder.add_edge("RecomputeMatchScore", END)

    return builder.compile(checkpointer=get_checkpointer())


def start_tailoring(
    thread_id: str,
    resume_id: uuid.UUID,
    user_id: uuid.UUID,
    structured_data: dict,
    jd_text: str,
    store: VectorStore | None = None,
    max_revisions: int = 2,
) -> dict:
    """Run to the first interrupt and return the paused payload."""
    graph = _make_tailor_graph(store, max_revisions)
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke(
        {
            "resume_id": str(resume_id),
            "user_id": str(user_id),
            "structured_data": structured_data,
            "jd_text": jd_text,
            "revision_round": 0,
            "trace": [],
        },
        config,
    )
    payload: dict[str, Any] = {
        "interrupted": "__interrupt__" in result,
        "suggestions": [],
        "match": result.get("match", {}),
        "keywords": result.get("keywords", []),
        "rejected": result.get("rejected", []),
        "trace": result.get("trace", []),
    }
    if "__interrupt__" in result:
        value = result["__interrupt__"][0].value
        payload["suggestions"] = value.get("suggestions", [])
        payload["match"] = value.get("match", payload["match"])
    else:
        payload["suggestions"] = result.get("approved", [])
    return payload


def resume_tailoring(
    thread_id: str, decision: str, store: VectorStore | None = None,
    max_revisions: int = 2,
) -> dict:
    """Resume a paused graph. Safe to call from a different process."""
    graph = _make_tailor_graph(store, max_revisions)
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke(Command(resume=decision), config)
    return {
        "match": result.get("match", {}),
        "decision": result.get("decision", ""),
        "trace": result.get("trace", []),
    }


def get_graph_state(thread_id: str, store: VectorStore | None = None) -> dict:
    graph = _make_tailor_graph(store, 2)
    snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    return {
        "next": list(snapshot.next),
        "finished": len(snapshot.next) == 0,
        "values": {k: v for k, v in snapshot.values.items() if k != "structured_data"},
    }
