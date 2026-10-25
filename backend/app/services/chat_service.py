import logging
import uuid

from app.config import settings
from app.repositories.agent_run_repository import AgentRunRepository
from app.repositories.chat_repository import ChatRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.suggestion_repository import SuggestionRepository
from app.repositories.tailoring_repository import TailoringRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vector_repository import VectorRepository
from app.services import resume_ops
from app.services.exceptions import QuotaExceeded, ResumeNotFound, ValidationError
from app.services.suggestion_service import SKILLS_PICKER_REF

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
        sessions: TailoringRepository | None = None,
    ) -> None:
        self.chats = chats
        self.resumes = resumes
        self.suggestions = suggestions
        self.users = users
        self.vectors = vectors
        self.runs = runs
        # Supplies the gap keywords behind the skills advice. Optional so the
        # service still constructs in tests that do not care about tailoring.
        self.sessions = sessions

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

        # Read the transcript BEFORE appending this turn, so the history
        # handed to the agent ends with the previous exchange rather than
        # repeating the message we are about to answer.
        history = [
            (m.role, m.content) for m in self.chats.recent_turns(resume_id, limit=10)
        ]
        self.chats.append(resume_id, "user", content.strip())

        reply, suggestion, degraded = self._respond(
            resume, content.strip(), user_id, history
        )
        suggestion_id = suggestion.id if suggestion is not None else None
        message = self.chats.append(
            resume_id,
            "assistant",
            reply,
            suggestion_id,
            # The pane reads this to show an "unreachable" notice with a
            # retry, instead of presenting an outage as a vague question.
            meta=(
                {
                    "kind": "agent_unreachable",
                    "retryable": True,
                    # Why, not just that. A rate limit, an expired key and a
                    # timeout need three different responses from the user,
                    # and the server log is not where they are looking.
                    "reason": degraded if isinstance(degraded, str) else "",
                }
                if degraded
                else None
            ),
        )

        # A reply the assistant could not actually produce is not worth one
        # of 25 messages. Rule-based replies still bill -- they answered.
        if not degraded:
            self.users.decrement_tokens(user, 1)
        self.db.commit()
        return {
            "message": message,
            "suggestion": suggestion,
            "tokens_left": user.chat_tokens_left,
        }

    def _respond(
        self,
        resume,
        prompt: str,
        user_id: uuid.UUID,
        history: list[tuple[str, str]] | None = None,
    ):
        """LLM agent when configured, rule-based assistant otherwise.

        Returns `(reply, suggestion, degraded)`. `degraded` means the agent
        was configured and attempted but could not be reached -- which is a
        different thing from the rule engine answering by design, and the
        user is entitled to know which one happened.
        """
        data = resume.structured_data or {}

        agent_reply, degraded = self._agent_respond(
            resume, prompt, user_id, data, history
        )
        if agent_reply is not None:
            return (*agent_reply, False)

        lowered = prompt.lower()

        # Skills first: "Suggest skills I am missing" is one of the quick
        # actions this backend itself advertises, and before this branch
        # existed it fell through to the generic "be more specific" reply --
        # the app offering a button it could not answer.
        if "skill" in lowered:
            return (*self._skills_advice(resume, data), degraded)

        if "summary" in lowered:
            current = data.get("summary", {}).get("text", "")
            if not current:
                return (
                    "This resume has no summary yet. Add one and I can sharpen it.",
                    None,
                    degraded,
                )
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
            return (
                "Here is a stronger summary — review it on the right.",
                suggestion,
                degraded,
            )

        if "bullet" in lowered or "quantify" in lowered or "impact" in lowered:
            return self._bullet_reply(resume, data, lowered, degraded)

        if not settings.llm_configured:
            return (
                "Running without an LLM key, so I can help with summaries, "
                "bullet rewrites and quantifying achievements. Try one of the "
                "quick actions.",
                None,
                degraded,
            )
        return (
            "Could you be more specific about which section to improve? "
            "I can work on your summary, a specific bullet, or your skills.",
            None,
            degraded,
        )

    # --------------------------------------------------------------- bullets
    def _bullet_reply(
        self,
        resume,
        data: dict,
        lowered: str,
        degraded,
    ):
        """Rule-based bullet help: strictly scoped, or a plain refusal.

        Two failure modes from the running app drove this shape:

        1. `iter_bullets` yields the summary as a FIRST, pseudo bullet, and
           the old fallback picked the LONGEST candidate. A summary is
           always the longest paragraph, so "shorten the bullets in my
           experience section" rewrote the summary -- the user watched the
           edit card say "Resume edited / Summary" after asking about
           experience. So the summary is excluded from this pool entirely,
           and an explicit section name in the prompt narrows the pool to
           that section.

        2. Bulk or keyword intents ("shorten ALL bullets", "add tailored
           keywords") are jobs that need judgement: what to cut, which
           keyword the job actually wants. A rule engine that silently
           prepends "Owned" to one line is not doing the job, it is faking
           it -- and the summary mangling showed fake edits land in real
           documents. Those get an honest refusal instead.
        """
        wants_projects = "project" in lowered
        wants_experience = "experience" in lowered

        # Bulk/keyword work is LLM or tailor-flow work, never a mechanical
        # one-line edit. Refuse clearly and say what CAN be done.
        if (
            "keyword" in lowered
            or (
                any(b in lowered for b in ("all", "every", "each"))
                and "bullet" in lowered
            )
        ):
            section = (
                "experience" if wants_experience else "projects" if wants_projects else None
            )
            scope = f"the {section} section of " if section else ""
            return (
                f"Weaving keywords into every bullet in {scope}this resume is "
                "rewriting work the built-in rules cannot do honestly — they "
                "will not shorten or keyword-match a whole section, and I will "
                "not fake it by touching one line. Use Tailor resume against "
                "the job description for keyword-matched bullets, or ask me to "
                "strengthen a specific bullet and I will draft that one.\n\n"
                "Note: this did not change anything in your resume.",
                None,
                degraded,
            )

        bullets = [
            b
            for b in resume_ops.iter_bullets(data)
            # The summary is a paragraph, not a bullet; the bullet pool must
            # never contain it or "the longest bullet" is the summary again.
            if b[0] != "summary.text"
        ]
        if wants_experience and not wants_projects:
            pool, scope_name = [b for b in bullets if b[0].startswith("exp_")], "Experience"
        elif wants_projects and not wants_experience:
            pool, scope_name = [b for b in bullets if b[0].startswith("prj_")], "Projects"
        else:
            pool, scope_name = bullets, None

        if not pool:
            if scope_name:
                return (
                    f"There are no bullets in the {scope_name} section yet, so "
                    "there is nothing for me to rework there. Add one in the "
                    "editor or import a resume that has it.",
                    None,
                    degraded,
                )
            return (
                "This resume has no experience or project bullets yet — add "
                "one in the editor and I can draft a stronger opening for it.",
                None,
                degraded,
            )

        target_ref, placement, text = max(pool, key=lambda b: len(b[2]))
        # No invented numbers: the critic rejects metrics absent from the
        # original, and the fallback must satisfy the same rule.
        improved = "Owned " + text[0].lower() + text[1:] if text else text
        section = "projects" if target_ref.startswith("prj_") else "experience"
        suggestion = self.suggestions.create(
            resume_id=resume.id,
            session_id=None,
            origin="chat",
            section=section,
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
        where = f" in {placement}" if placement else ""
        if scope_name:
            where = f" in the {scope_name} section{where}"
        return (
            f"I drafted a stronger opening for the longest bullet{where}. "
            "Add the real number where it fits — I will not invent one.",
            suggestion,
            degraded,
        )

    # --------------------------------------------------------------- skills
    def _skills_advice(self, resume, data: dict):
        """Advisory answer for "Suggest skills I am missing".

        Returns text, never a Suggestion. The honest answer to "what skills
        am I missing" requires knowing what the candidate can truthfully
        claim, and no model knows that -- an agent free to add skills invents
        them. So this reports the gap between a real job description and the
        resume, and leaves the judgement with the user.
        """
        have = {
            str(item).strip().lower()
            for group in (data.get("skills") or [])
            if isinstance(group, dict)
            for item in (group.get("items") or [])
            if str(item).strip()
        }

        gaps: list[str] = []
        if self.sessions is not None:
            try:
                sessions = self.sessions.list_for_resume(resume.id)
                for session in sessions:
                    for kw in session.gap_keywords or []:
                        low = str(kw).strip().lower()
                        if low and low not in have and kw not in gaps:
                            gaps.append(kw)
                    if gaps:
                        break
            except Exception as exc:  # noqa: BLE001
                logger.warning("skills advice could not read sessions: %s", exc)

        if gaps:
            chosen = gaps[:12]
            # Reuse the existing clarifying-question machinery rather than
            # emitting an AI-authored draft. `needs_input` means "this card is
            # a QUESTION, not a draft": nothing can be applied until the user
            # answers, and what they answer is recorded as theirs.
            #
            # The keywords ride on the card, so the chooser renders from the
            # server's list and a client cannot widen it.
            existing = self._open_skills_card(resume.id)
            if existing is not None:
                existing.keywords = chosen
                self.db.flush()
                suggestion = existing
            else:
                suggestion = self.suggestions.create(
                    resume_id=resume.id,
                    session_id=None,
                    origin="chat",
                    section="skills",
                    # No single target_ref: the choices can land in different
                    # groups. The apply step resolves each one.
                    target_ref=SKILLS_PICKER_REF,
                    placement="Skills",
                    original_text="",
                    suggested_text="",
                    keywords=chosen,
                    reasoning=(
                        "These appear in the job description you tailored "
                        "against but not in your skills."
                    ),
                    status="needs_input",
                    grounded=True,
                    question=(
                        "Which of these can you back up? I will add only the "
                        "ones you pick, and record them as yours."
                    ),
                    requires_text=True,
                )
            return (
                "Comparing your resume with the job description you tailored "
                "against, these appear in the posting but not in your skills. "
                "Pick the ones you can genuinely back up — I add only what you "
                "select, and only you know which of these you can defend in "
                "an interview.",
                suggestion,
            )

        if not have:
            return (
                "Your resume has no skills listed yet. Add a Skills section "
                "with the tools and languages you actually use, then tailor "
                "against a job description and I can show you what the "
                "posting asks for that you have not listed.",
                None,
            )

        return (
            "To tell you what is missing I need something to compare against. "
            "Paste a job description in the Tailor tab and run it — then ask "
            "me again and I will list the skills that posting wants which "
            f"your resume does not mention. Right now you list {len(have)} "
            "skills.",
            None,
        )

    def _open_skills_card(self, resume_id: uuid.UUID):
        """An unanswered skills card already on this resume, if any.

        Asking twice must refresh the one card rather than stack duplicates
        that all write to the same section.
        """
        from app.models import Suggestion

        return (
            self.db.query(Suggestion)
            .filter(
                Suggestion.resume_id == resume_id,
                # target_ref, not section: a tailoring clarifying card is
                # also section="skills" and must not be reused or clobbered.
                Suggestion.target_ref == SKILLS_PICKER_REF,
                Suggestion.status == "needs_input",
            )
            .first()
        )

    # ----------------------------------------------------------------- agent
    def _agent_respond(
        self,
        resume,
        prompt: str,
        user_id: uuid.UUID,
        data: dict,
        history: list[tuple[str, str]] | None = None,
    ):
        """Chat agent path.

        Returns `(reply_tuple_or_None, degraded)`. `None` means fall through
        to the rule-based reply; `degraded` says whether that fall-through
        was caused by a failure rather than by design.

        A proposed edit is only persisted when the critic approves it, so the
        chat assistant cannot introduce a suggestion the tailor flow would have
        rejected.
        """
        if not settings.llm_configured:
            return (None, False)
        try:
            from app.ai import agents
            from app.ai.vectorstore import VectorStore

            store = VectorStore(self.vectors) if self.vectors is not None else None
            outcome = agents.chat_reply(
                prompt,
                data,
                user_id=user_id,
                resume_id=resume.id,
                store=store,
                history=history,
            )
            reply = outcome.value
            if reply is None:
                reason = (outcome.notes or [""])[0] if outcome.status == "degraded" else None
                return (None, reason or None)

            self._record(outcome, user_id)

            if not reply.proposes_change:
                return ((reply.message, None), False)

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
                return ((reply.message, None), False)

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
            return ((reply.message, suggestion), False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Chat agent unavailable, using rule-based reply: %s", exc)
            return (None, agents._failure_reason(exc))

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
