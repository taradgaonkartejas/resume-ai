import logging
import uuid

from app.models import AnalysisReport
from app.repositories.agent_run_repository import AgentRunRepository
from app.repositories.analysis_repository import AnalysisRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.vector_repository import VectorRepository
from app.services.exceptions import NotFoundError, ResumeNotFound
from app.services.heuristics import infer_role_tags, score_resume

logger = logging.getLogger(__name__)


class AnalysisService:
    """Scoring pipeline: RetrieveATS -> RuleEngine -> ScoringAgent.

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
        data = resume.structured_data or {}

        result, role_tags, trace = self._run_graph(data, user_id)

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
                    "graph": "analysis",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Analysis graph unavailable, running inline: %s", exc)
            return (
                score_resume(data),
                infer_role_tags(data),
                {
                    "nodes": ["RetrieveATS", "RuleEngine", "ScoringAgent"],
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

    def latest(self, resume_id: uuid.UUID, user_id: uuid.UUID) -> AnalysisReport:
        self._owned(resume_id, user_id)
        report = self.analyses.latest_for_resume(resume_id)
        if report is None:
            raise NotFoundError("No analysis yet — POST /analyze first")
        return report
