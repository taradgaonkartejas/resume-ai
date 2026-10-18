import logging
import uuid

from app.models import AnalysisReport
from app.repositories.agent_run_repository import AgentRunRepository
from app.repositories.analysis_repository import AnalysisRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.vector_repository import VectorRepository
from app.services.exceptions import NotFoundError, ResumeNotFound, ResumeNotReady
from app.services.heuristics import (
    build_steps,
    data_fingerprint,
    infer_role_tags,
    score_resume,
)

logger = logging.getLogger(__name__)


def _require_parsed(resume) -> None:
    """Refuse to score a resume we have not successfully read.

    Without this, a "pending" or "failed" resume holds empty_resume() and scores
    near zero -- indistinguishable, to the user, from a genuinely weak resume.
    """
    status = getattr(resume, "parse_status", "ready")
    if status == "ready":
        return
    if status == "failed":
        note = (getattr(resume, "parse_note", "") or "").strip()
        raise ResumeNotReady(
            f"This resume could not be read, so it cannot be analysed. {note}".strip()
        )
    raise ResumeNotReady("This resume is still being processed. Try again in a moment.")


class AnalysisService:
    """Scoring pipeline: RuleEngine -> ScoringAgent.

    The numeric score comes from the rule engine only, so it is reproducible.
    An LLM, when configured, contributes prose commentary — never the number.

    The LangGraph implementation is used when the AI layer imports cleanly;
    if it does not, the same three stages run inline. Either way the score for
    a given resume is identical, which is what keeps the contract stable.
    """

    def __init__(
        self,
        analyses: AnalysisRepository,
        resumes: ResumeRepository,
        vectors: VectorRepository | None = None,
        runs: AgentRunRepository | None = None,
    ) -> None:
        self.analyses = analyses
        self.resumes = resumes
        self.vectors = vectors
        self.runs = runs

    def _owned(self, resume_id: uuid.UUID, user_id: uuid.UUID):
        resume = self.resumes.get_owned(resume_id, user_id)
        if resume is None:
            raise ResumeNotFound(str(resume_id))
        return resume

    def analyze(self, resume_id: uuid.UUID, user_id: uuid.UUID) -> AnalysisReport:
        resume = self._owned(resume_id, user_id)
        _require_parsed(resume)
        data = resume.structured_data or {}

        result, role_tags, trace = self._run_graph(data, user_id)

        # Record WHAT this commentary was written about. The prose in `trace`
        # describes this exact snapshot; the findings shown beside it are
        # recomputed live. Without this the UI cannot tell the user their
        # "Overall Assessment" is describing a resume they have since edited.
        # `trace` is a JSON column, so this costs no migration.
        trace = {**trace, "data_fingerprint": data_fingerprint(data)}

        report = self.analyses.create(
            resume_id=resume.id,
            overall_score=result["overall_score"],
            category_scores=result["category_scores"],
            role_tags=role_tags,
            trace=trace,
        )
        self.resumes.db.commit()
        return report

    def _run_graph(
        self, data: dict, user_id: uuid.UUID
    ) -> tuple[dict, list[str], dict]:
        try:
            from app.ai.graphs import run_analysis
            from app.ai.vectorstore import VectorStore

            store = VectorStore(self.vectors) if self.vectors is not None else None
            state = run_analysis(data, store)
            nodes = [step["node"] for step in state.get("trace", [])]
            used_llm = any(step.get("used_llm") for step in state.get("trace", []))
            self._record(state.get("trace", []), user_id)
            return (
                state["rule_result"],
                state.get("role_tags", []),
                {
                    "nodes": nodes,
                    "deterministic": True,
                    "llm_used": used_llm,
                    "commentary": state.get("commentary", {}),
                    "ats_review": state.get("ats_review", {}),
                    "graph": "analysis",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Analysis graph unavailable, running inline: %s", exc)
            return (
                score_resume(data),
                infer_role_tags(data),
                {
                    "nodes": ["RuleEngine", "ScoringAgent", "AtsAgent"],
                    "deterministic": True,
                    "llm_used": False,
                },
            )

    def _record(self, trace: list, user_id: uuid.UUID) -> None:
        if self.runs is None:
            return
        for step in trace:
            if not step.get("agent"):
                continue
            try:
                self.runs.record(
                    user_id=user_id,
                    agent=step["agent"],
                    task="analysis",
                    model=step.get("model", ""),
                    latency_ms=step.get("latency_ms", 0),
                    tokens_in=step.get("tokens_in", 0),
                    tokens_out=step.get("tokens_out", 0),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("agent_run telemetry failed: %s", exc)

    def latest(self, resume_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        """The stored report, with the DETERMINISTIC half recomputed.

        One number, one way of producing it. The score is a pure function of
        structured_data (heuristics.py, fixed weights), so serving a copy
        frozen at the last POST /analyze is serving a second, lying source of
        truth — and `steps()` directly below already recomputes, which is how
        the two endpoints came to disagree.

        The bug that forced this: fix recommendations in the guided editor and
        the score climbs 79 -> 93, because the stepper reads the live steps.
        Switch to the Tailor tab and back, and the Analyze tab says 79 again,
        because it reads this row. Nothing is wrong with the cache — an
        invalidation just refetches the same stale number — and the user is
        told their fixes did nothing until they press "Improve my resume".

        So the split is drawn where the cost is, not where the storage is:

          deterministic (score, category_scores, notes, findings)
              -> recomputed here on every call, always current
          LLM-authored prose (commentary, ats_review, role_tags)
              -> a genuine snapshot; regenerating costs a model call per
                 page view, so it keeps its staleness stamp instead

        Returns a dict rather than the ORM row ON PURPOSE. Overlaying the
        fresh numbers onto the AnalysisReport instance would mark it dirty and
        the next commit would silently rewrite the stored report, turning a
        read into a write and destroying the historical record the row exists
        to be.
        """
        resume = self._owned(resume_id, user_id)
        report = self.analyses.latest_for_resume(resume_id)
        if report is None:
            raise NotFoundError("No analysis yet — POST /analyze first")

        data = resume.structured_data or {}
        scored = score_resume(data)
        # Same helper the guided editor uses, so the two screens cannot
        # disagree about whether the prose still describes the resume.
        _, stale = self._commentary_for(resume_id, data)

        return {
            "id": report.id,
            "resume_id": report.resume_id,
            "overall_score": scored["overall_score"],
            "category_scores": scored["category_scores"],
            "role_tags": report.role_tags or [],
            "trace": report.trace or {},
            "created_at": report.created_at,
            "commentary_stale": stale,
        }

    def steps(self, resume_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        """The guided editor's step list, scored against the CURRENT resume.

        Deliberately not read from the stored AnalysisReport. The editor mutates
        structured_data on every keystroke (debounced autosave), so a cached
        report is stale the moment the user fixes anything — and a stale
        "+3 points" card that never disappears is exactly the bug that makes
        the score feel fake. Scoring is pure and cheap, so recompute.
        """
        resume = self._owned(resume_id, user_id)
        data = resume.structured_data or {}
        scored = score_resume(data)
        steps = build_steps(data, scored)

        # The LLM commentary rides along with the live steps.
        #
        # It was already being computed, persisted and returned by GET
        # /analysis -- and no component ever rendered it, so we have been
        # paying for a model call per analysis and discarding the output.
        # The guided editor is the one screen where per-category advice is
        # actually useful, and it is the screen that never had it.
        #
        # It is a SNAPSHOT riding next to LIVE findings, so it ships with its
        # own staleness flag rather than pretending to be current.
        commentary, stale = self._commentary_for(resume.id, data)

        return {
            "resume_id": resume.id,
            "overall_score": scored["overall_score"],
            "max_score": sum(s["max"] for s in steps),
            # Total honest headroom the guided flow can still recover.
            "points_available": sum(s["points_available"] for s in steps),
            "steps": steps,
            "commentary": commentary,
            "commentary_stale": stale,
        }

    def _commentary_for(self, resume_id: uuid.UUID, data: dict) -> tuple[dict, bool]:
        """Latest stored commentary, plus whether the resume moved under it.

        Three states, kept distinct on purpose:

          no report / no prose  -> ({}, False)   nothing to show, nothing to warn about
          fingerprint matches   -> (prose, False) provably describes this text
          fingerprint differs   -> (prose, True)  provably out of date

        A report written before fingerprints existed has no way to prove
        either, and is reported as stale. That is the safe direction: an
        unnecessary "from your last analysis" note costs the user nothing,
        whereas silently presenting stale prose as current is the exact
        failure this flag exists to prevent.
        """
        report = self.analyses.latest_for_resume(resume_id)
        if report is None:
            return {}, False
        trace = report.trace or {}
        commentary = trace.get("commentary") or {}
        if not isinstance(commentary, dict) or not commentary:
            return {}, False
        recorded = trace.get("data_fingerprint")
        return commentary, recorded != data_fingerprint(data)
