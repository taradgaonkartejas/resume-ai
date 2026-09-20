import logging
import uuid

from app.models import TailoringSession
from app.repositories.agent_run_repository import AgentRunRepository
from app.repositories.job_description_repository import JobDescriptionRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.suggestion_repository import SuggestionRepository
from app.repositories.tailoring_repository import TailoringRepository
from app.repositories.vector_repository import VectorRepository
from app.services import resume_ops
from app.services.exceptions import ResumeNotFound, ValidationError
from app.services.heuristics import extract_keywords, match_keywords

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 6

GRAPH_NODES = [
    "ParseJD",
    "ExtractKeywords",
    "RetrieveResumeContext",
    "GenerateSuggestions",
    "CriticReview",
    "AwaitUserReview",
]


class TailoringService:
    """Starts a tailoring session and runs to the first human interrupt.

    The graph pauses at AwaitUserReview and the pause is checkpointed to disk,
    so the Accept/Reject/Edit requests that arrive minutes later — in a
    different worker, or after a restart — resume the same run.

    Without an LLM key the deterministic writer runs instead: gap keywords are
    woven into existing bullets. Either path is filtered by the same critic
    checks, so a suggestion whose target_ref does not resolve, or whose
    original_text does not match the resume verbatim, is never persisted.
    """

    def __init__(
        self,
        sessions: TailoringRepository,
        suggestions: SuggestionRepository,
        resumes: ResumeRepository,
        jds: JobDescriptionRepository,
        vectors: VectorRepository | None = None,
        runs: AgentRunRepository | None = None,
    ) -> None:
        self.sessions = sessions
        self.suggestions = suggestions
        self.resumes = resumes
        self.jds = jds
        self.vectors = vectors
        self.runs = runs

    @property
    def db(self):
        return self.sessions.db

    def list_sessions(self, resume_id: uuid.UUID, user_id: uuid.UUID):
        if self.resumes.get_owned(resume_id, user_id) is None:
            raise ResumeNotFound(str(resume_id))
        return self.sessions.list_for_resume(resume_id)

    def start(
        self,
        resume_id: uuid.UUID,
        user_id: uuid.UUID,
        jd_title: str,
        jd_content: str,
    ) -> TailoringSession:
        resume = self.resumes.get_owned(resume_id, user_id)
        if resume is None:
            raise ResumeNotFound(str(resume_id))
        if not jd_content or not jd_content.strip():
            raise ValidationError("Job description content is required")

        data = resume.structured_data or {}
        thread_id = f"tailor-{uuid.uuid4()}"

        outcome = self._run_graph(thread_id, resume.id, user_id, data, jd_content)
        keywords = outcome["keywords"]
        scores = outcome["match"]
        drafts = outcome["suggestions"]

        jd = self.jds.create(
            title=jd_title or "Untitled role",
            content=jd_content,
            extracted_keywords=keywords,
        )

        session = self.sessions.create(
            resume_id=resume.id,
            job_description_id=jd.id,
            thread_id=thread_id,
            match_percent=scores["match_percent"],
            baseline_percent=scores["match_percent"],
            matched_keywords=scores["matched_keywords"],
            gap_keywords=scores["gap_keywords"],
            all_keywords=keywords,
            graph_state="awaiting_review",
            trace=outcome["trace"],
        )

        for draft in drafts:
            # Critic hard checks, re-applied at the persistence boundary.
            if not resume_ops.exists(data, draft["target_ref"]):
                continue
            if resume_ops.resolve(data, draft["target_ref"]) != draft["original_text"]:
                continue
            self.suggestions.create(
                resume_id=resume.id,
                session_id=session.id,
                origin="tailor",
                section=draft["section"],
                target_ref=draft["target_ref"],
                placement=draft["placement"],
                original_text=draft["original_text"],
                suggested_text=draft["suggested_text"],
                keywords=draft["keywords"],
                reasoning=draft["reasoning"],
                status="pending",
                grounded=True,
            )

        self.db.commit()
        return session

    # ------------------------------------------------------------------ graph
    def _run_graph(
        self,
        thread_id: str,
        resume_id: uuid.UUID,
        user_id: uuid.UUID,
        data: dict,
        jd_content: str,
    ) -> dict:
        """Run the LangGraph tailor graph, or the inline path if it is absent."""
        try:
            from app.ai.graphs import start_tailoring
            from app.ai.vectorstore import VectorStore

            store = VectorStore(self.vectors) if self.vectors is not None else None
            payload = start_tailoring(
                thread_id=thread_id,
                resume_id=resume_id,
                user_id=user_id,
                structured_data=data,
                jd_text=jd_content,
                store=store,
            )
            self._record(payload.get("trace", []), user_id, thread_id)
            steps = payload.get("trace", [])
            return {
                "keywords": payload["keywords"],
                "match": payload["match"],
                "suggestions": payload["suggestions"],
                "trace": {
                    "nodes": [s["node"] for s in steps],
                    "llm_used": any(s.get("used_llm") for s in steps),
                    "rejected": len(payload.get("rejected", [])),
                    "interrupted": payload.get("interrupted", False),
                    "graph": "tailor",
                },
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tailor graph unavailable, running inline: %s", exc)
            keywords = extract_keywords(jd_content)
            scores = match_keywords(data, keywords)
            return {
                "keywords": keywords,
                "match": scores,
                "suggestions": self._draft_suggestions(data, scores["gap_keywords"]),
                "trace": {"nodes": GRAPH_NODES, "llm_used": False},
            }

    def _record(self, trace: list, user_id: uuid.UUID, thread_id: str) -> None:
        if self.runs is None:
            return
        for step in trace:
            if not step.get("agent"):
                continue
            try:
                self.runs.record(
                    user_id=user_id,
                    agent=step["agent"],
                    task="tailor",
                    model=step.get("model", ""),
                    latency_ms=step.get("latency_ms", 0),
                    tokens_in=step.get("tokens_in", 0),
                    tokens_out=step.get("tokens_out", 0),
                    thread_id=thread_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("agent_run telemetry failed: %s", exc)

    def _draft_suggestions(self, data: dict, gaps: list[str]) -> list[dict]:
        """Deterministic writer used when the graph cannot run."""
        if not gaps:
            return []
        drafts: list[dict] = []
        for target_ref, placement, text in resume_ops.iter_bullets(data):
            if len(drafts) >= MAX_SUGGESTIONS:
                break
            if not text.strip():
                continue
            chunk = gaps[len(drafts) : len(drafts) + 2]
            if not chunk:
                break
            joined = " and ".join(chunk)
            section = "summary" if target_ref.startswith("summary") else (
                "projects" if target_ref.startswith("prj") else "experience"
            )
            drafts.append(
                {
                    "section": section,
                    "target_ref": target_ref,
                    "placement": placement,
                    "original_text": text,
                    "suggested_text": text.rstrip(".") + f", leveraging {joined}.",
                    "keywords": chunk,
                    "reasoning": f"Adds missing job-description keyword(s): {joined}.",
                }
            )
        return drafts
