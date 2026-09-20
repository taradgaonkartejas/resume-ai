import logging
import uuid

from app.config import settings
from app.repositories.agent_run_repository import AgentRunRepository
from app.repositories.chat_repository import ChatRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.suggestion_repository import SuggestionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vector_repository import VectorRepository
from app.services import resume_ops
from app.services.exceptions import QuotaExceeded, ResumeNotFound, ValidationError

logger = logging.getLogger(__name__)

QUICK_ACTIONS = [
    "Make my summary more impactful",
    "Quantify my top achievement",
    "Tighten my longest bullet",
    "Suggest skills I am missing",
]


class ChatService:
    """Chat assistant. Emits the same Suggestion objects as the tailor flow,
    so the Accept/Reject/Edit UI is identical — only `origin` differs.
    """

    def __init__(
        self,
        chats: ChatRepository,
        resumes: ResumeRepository,
        suggestions: SuggestionRepository,
        users: UserRepository,
        vectors: VectorRepository | None = None,
        runs: AgentRunRepository | None = None,
    ) -> None:
        self.chats = chats
        self.resumes = resumes
        self.suggestions = suggestions
        self.users = users
        self.vectors = vectors
        self.runs = runs

    @property
    def db(self):
        return self.chats.db

    def _owned(self, resume_id: uuid.UUID, user_id: uuid.UUID):
        resume = self.resumes.get_owned(resume_id, user_id)
        if resume is None:
            raise ResumeNotFound(str(resume_id))
        return resume

    def history(self, resume_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        self._owned(resume_id, user_id)
        user = self.users.get(user_id)
        return {
            "messages": self.chats.list_for_resume(resume_id),
            "quick_actions": QUICK_ACTIONS,
            "tokens_left": user.chat_tokens_left if user else 0,
        }

    def send(self, resume_id: uuid.UUID, user_id: uuid.UUID, content: str) -> dict:
        if not content or not content.strip():
            raise ValidationError("Message cannot be empty")
        resume = self._owned(resume_id, user_id)
        user = self.users.get(user_id)
        if user is None or user.chat_tokens_left <= 0:
            raise QuotaExceeded("No chat tokens left for this user")

        self.chats.append(resume_id, "user", content.strip())

        reply, suggestion = self._respond(resume, content.strip(), user_id)
        suggestion_id = suggestion.id if suggestion is not None else None
        message = self.chats.append(resume_id, "assistant", reply, suggestion_id)

        self.users.decrement_tokens(user, 1)
        self.db.commit()
        return {
            "message": message,
            "suggestion": suggestion,
            "tokens_left": user.chat_tokens_left,
        }

    def _respond(self, resume, prompt: str, user_id: uuid.UUID):
        """LLM agent when configured, rule-based assistant otherwise."""
        data = resume.structured_data or {}

        agent_reply = self._agent_respond(resume, prompt, user_id, data)
        if agent_reply is not None:
            return agent_reply

        lowered = prompt.lower()

        if "summary" in lowered:
            current = data.get("summary", {}).get("text", "")
            if not current:
                return ("This resume has no summary yet. Add one and I can sharpen it.", None)
            improved = current.rstrip(".") + ", with measurable delivery impact."
            suggestion = self.suggestions.create(
                resume_id=resume.id,
                session_id=None,
                origin="chat",
                section="summary",
                target_ref="summary.text",
                placement="Summary",
                original_text=current,
                suggested_text=improved,
                keywords=[],
                reasoning="Strengthens the summary with an outcome-oriented close.",
                status="pending",
                grounded=True,
            )
            return ("Here is a stronger summary — review it on the right.", suggestion)

        bullets = list(resume_ops.iter_bullets(data))
        if bullets and (
            "bullet" in lowered or "quantify" in lowered or "impact" in lowered
        ):
            target_ref, placement, text = max(bullets, key=lambda b: len(b[2]))
            # No invented numbers: the critic rejects metrics absent from the
            # original, and the fallback must satisfy the same rule.
            improved = "Owned " + text[0].lower() + text[1:] if text else text
            suggestion = self.suggestions.create(
                resume_id=resume.id,
                session_id=None,
                origin="chat",
                section="experience",
                target_ref=target_ref,
                placement=placement,
                original_text=text,
                suggested_text=improved,
                keywords=[],
                reasoning=(
                    "Leads with ownership; add your own metric to finish it."
                ),
                status="pending",
                grounded=True,
            )
            return (
                "I drafted a stronger opening for that bullet. Add the real "
                "number where it fits — I will not invent one.",
                suggestion,
            )

        if not settings.llm_configured:
            return (
                "Running without an LLM key, so I can help with summaries, "
                "bullet rewrites and quantifying achievements. Try one of the "
                "quick actions.",
                None,
            )
        return ("Could you be more specific about which section to improve?", None)

    # ----------------------------------------------------------------- agent
    def _agent_respond(self, resume, prompt: str, user_id: uuid.UUID, data: dict):
        """Chat agent path. Returns None to fall through to the rule-based reply.

        A proposed edit is only persisted when the critic approves it, so the
        chat assistant cannot introduce a suggestion the tailor flow would have
        rejected.
        """
        if not settings.llm_configured:
            return None
        try:
            from app.ai import agents
            from app.ai.vectorstore import VectorStore

            store = VectorStore(self.vectors) if self.vectors is not None else None
            outcome = agents.chat_reply(
                prompt, data, user_id=user_id, resume_id=resume.id, store=store
            )
            reply = outcome.value
            if reply is None:
                return None

            self._record(outcome, user_id)

            if not reply.proposes_change:
                return (reply.reply, None)

            draft = {
                "target_ref": reply.target_ref,
                "original_text": resume_ops.resolve(data, reply.target_ref)
                if resume_ops.exists(data, reply.target_ref)
                else "",
                "suggested_text": reply.suggested_text,
                "keywords": [],
            }
            verdict = agents.review_draft(draft, data).value
            if not verdict.approved:
                logger.info("chat suggestion dropped by critic: %s", verdict.notes)
                return (reply.reply, None)

            section = (
                "summary"
                if reply.target_ref.startswith("summary")
                else "projects"
                if reply.target_ref.startswith("prj")
                else "experience"
            )
            placement = next(
                (p for r, p, _ in resume_ops.iter_bullets(data) if r == reply.target_ref),
                "",
            )
            suggestion = self.suggestions.create(
                resume_id=resume.id,
                session_id=None,
                origin="chat",
                section=section,
                target_ref=reply.target_ref,
                placement=placement,
                original_text=draft["original_text"],
                suggested_text=reply.suggested_text,
                keywords=[],
                reasoning=reply.reasoning or "Chat-suggested rewrite.",
                status="pending",
                grounded=True,
            )
            return (reply.reply, suggestion)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Chat agent unavailable, using rule-based reply: %s", exc)
            return None

    def _record(self, outcome, user_id: uuid.UUID) -> None:
        if self.runs is None:
            return
        try:
            self.runs.record(
                user_id=user_id,
                agent=outcome.agent,
                task="chat",
                model=outcome.model,
                latency_ms=outcome.latency_ms,
                tokens_in=outcome.tokens_in,
                tokens_out=outcome.tokens_out,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_run telemetry failed: %s", exc)
