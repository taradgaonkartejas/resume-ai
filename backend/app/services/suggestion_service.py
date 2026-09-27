import uuid

from app.models import Suggestion
from app.repositories.resume_repository import ResumeRepository
from app.repositories.suggestion_repository import SuggestionRepository
from app.repositories.tailoring_repository import TailoringRepository
from app.services import resume_ops
from app.services.exceptions import (
    InvalidStateTransition,
    InvalidTargetRef,
    ResumeNotFound,
    ResumeNotReady,
    RewriteRejected,
    SessionNotFound,
    StaleSuggestion,
    SuggestionNotFound,
    ValidationError,
)
from app.services.heuristics import match_keywords
from app.services.version_service import VersionService

TERMINAL = {"accepted", "rejected"}


def _finding_for(data: dict, finding_id: str, target_ref: str) -> dict:
    """The finding driving this rewrite, so the prompt knows the problem.

    Falls back to a generic instruction rather than failing: a rewrite with a
    vague brief is still useful, and the critic guards the output either way.
    """
    from app.services.heuristics import score_resume

    findings = score_resume(data).get("findings") or []
    if finding_id:
        for f in findings:
            if f.get("id") == finding_id:
                return f
    for f in findings:
        if f.get("target_ref") == target_ref:
            return f
    return {"id": finding_id, "message": "Improve this section", "fix_hint": ""}


class SuggestionService:
    """Owns the Accept / Reject / Edit lifecycle.

    Accept is the only write in the system that spans three tables. It runs as
    one transaction: patch the resume JSON, append a version, mark the
    suggestion, recompute the session match score. A suggestion marked accepted
    whose text never landed is the worst possible state, so nothing is
    committed until every step has succeeded.
    """

    def __init__(
        self,
        suggestions: SuggestionRepository,
        resumes: ResumeRepository,
        sessions: TailoringRepository,
        versions: VersionService,
    ) -> None:
        self.suggestions = suggestions
        self.resumes = resumes
        self.sessions = sessions
        self.versions = versions

    @property
    def db(self):
        return self.suggestions.db

    def list_for_session(
        self, session_id: uuid.UUID, user_id: uuid.UUID
    ) -> dict[str, list[Suggestion]]:
        session = self.sessions.get_owned(session_id, user_id)
        if session is None:
            raise SessionNotFound(str(session_id))
        rows = self.suggestions.list_for_session(session_id)
        return {
            "active": [s for s in rows if s.status == "pending"],
            "matched": [s for s in rows if s.status == "accepted"],
            "rejected": [s for s in rows if s.status == "rejected"],
        }

    def _require(self, suggestion_id: uuid.UUID, user_id: uuid.UUID) -> Suggestion:
        suggestion = self.suggestions.get_owned(suggestion_id, user_id)
        if suggestion is None:
            raise SuggestionNotFound(str(suggestion_id))
        return suggestion

    # ------------------------------------------------------- targeted rewrite
    def rewrite_section(
        self,
        resume_id: uuid.UUID,
        user_id: uuid.UUID,
        target_ref: str,
        finding_id: str = "",
        regenerate: bool = False,
    ) -> Suggestion:
        """Draft an AI fix for one finding and persist it as a Suggestion.

        Lives here, not in AnalysisService, because the thing it produces is a
        Suggestion and this class owns that lifecycle. It returns the ordinary
        Suggestion shape so the frontend reuses PATCH /suggestions/{id}: there
        is exactly one accept/reject/edit path, not two.
        """
        from app.ai import agents

        resume = self.resumes.get_owned(resume_id, user_id)
        if resume is None:
            raise ResumeNotFound(str(resume_id))
        status = getattr(resume, "parse_status", "ready")
        if status != "ready":
            raise ResumeNotReady(
                "This resume has not been read successfully, so there is "
                "nothing to rewrite yet."
            )

        data = resume.structured_data or {}
        if not resume_ops.exists(data, target_ref):
            raise InvalidTargetRef(f"{target_ref}: nothing to rewrite there")

        # One pending analysis draft per ref. Re-clicking returns the existing
        # one rather than stacking duplicates on the same bullet; Regenerate
        # supersedes it explicitly.
        existing = self._pending_analysis(resume.id, target_ref)
        if existing is not None:
            if not regenerate:
                return existing
            existing.status = "rejected"
            self.db.flush()

        finding = _finding_for(data, finding_id, target_ref)
        outcome = agents.rewrite_for_finding(
            data, target_ref, finding, user_id=user_id, resume_id=resume.id
        )
        draft = dict(outcome.value)
        draft["target_ref"] = target_ref

        if not (draft.get("suggested_text") or "").strip():
            raise RewriteRejected("Could not draft a rewrite for this section.")
        if draft["suggested_text"].strip() == draft["original_text"].strip():
            # Most often the AI writer is unavailable and the deterministic
            # fallback has no mechanical fix for this finding -- it cannot
            # invent a metric, and should not pretend otherwise.
            raise RewriteRejected(
                "This one needs a fact only you have - the AI writer cannot "
                "add a number that is not already in your resume. Edit the "
                "text directly instead."
            )

        # The same grounding guard the tailoring graph uses. An ungrounded
        # draft is never persisted, so the user never sees it.
        verdict = agents.review_draft(draft, data).value
        if not verdict.approved:
            raise RewriteRejected(f"The reviewer rejected this rewrite: {verdict.notes}")

        suggestion = self.suggestions.create(
            resume_id=resume.id,
            session_id=None,
            origin="analysis",
            section=target_ref.split(".")[0],
            target_ref=target_ref,
            placement=finding.get("message", ""),
            original_text=draft["original_text"],
            suggested_text=draft["suggested_text"].strip(),
            keywords=[k for k in (draft.get("keywords") or []) if k][:8],
            reasoning=draft.get("reasoning") or "",
            status="pending",
            grounded=True,
            # Degraded writer: say so on the row rather than in a log line, so
            # the card can label it "written without AI".
            critic_notes="" if outcome.used_llm else "written without AI",
        )
        self.db.commit()
        return suggestion

    def _pending_analysis(self, resume_id: uuid.UUID, target_ref: str):
        return (
            self.db.query(Suggestion)
            .filter(
                Suggestion.resume_id == resume_id,
                Suggestion.target_ref == target_ref,
                Suggestion.origin == "analysis",
                Suggestion.status == "pending",
            )
            .first()
        )


    def act(
        self,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
        action: str,
        edited_text: str | None = None,
    ) -> Suggestion:
        action = action.lower()
        if action not in {"accept", "reject", "edit"}:
            raise ValidationError(f"Unknown action: {action}")

        suggestion = self._require(suggestion_id, user_id)
        if suggestion.status in TERMINAL and action != "edit":
            raise InvalidStateTransition(
                f"Suggestion already {suggestion.status}"
            )

        if action == "reject":
            suggestion.status = "rejected"
            self.db.commit()
            return suggestion

        if action == "edit":
            if not edited_text or not edited_text.strip():
                raise ValidationError("edited_text is required for edit")
            suggestion.edited_text = edited_text.strip()
            suggestion.status = "pending"
            self.db.commit()
            return suggestion

        # --- accept: one transaction across three tables ---
        resume = self.resumes.get_owned(suggestion.resume_id, user_id)
        if resume is None:
            raise SuggestionNotFound(str(suggestion_id))

        text = suggestion.edited_text or suggestion.suggested_text
        if not text.strip():
            raise ValidationError("Suggestion has no text to apply")

        data = resume.structured_data or {}

        # The suggestion was written against original_text. If the field no
        # longer holds that, someone edited it in the meantime -- by hand, via
        # chat, or by accepting an overlapping suggestion -- and applying this
        # patch would silently destroy that newer work. apply_patch only checks
        # that the *path* still resolves, not that the *content* is unchanged.
        if resume_ops.exists(data, suggestion.target_ref):
            current = resume_ops.resolve(data, suggestion.target_ref)
            if current != suggestion.original_text:
                raise StaleSuggestion(
                    "This text changed after the suggestion was generated, so "
                    "accepting it would overwrite the newer version. Reject it "
                    "and re-run tailoring to get a suggestion for the current "
                    "text."
                )

        # Raises InvalidTargetRef if the path vanished since generation.
        patched = resume_ops.apply_patch(data, suggestion.target_ref, text)

        resume.structured_data = patched
        self.versions.record(
            resume,
            patched,
            change_source=suggestion.origin or "tailor",
            label=f"Accepted: {suggestion.target_ref}",
        )
        suggestion.status = "accepted"

        if suggestion.session_id:
            session = self.sessions.get_owned(suggestion.session_id, user_id)
            if session is not None and session.all_keywords:
                scores = match_keywords(patched, list(session.all_keywords))
                session.match_percent = scores["match_percent"]
                session.matched_keywords = scores["matched_keywords"]
                session.gap_keywords = scores["gap_keywords"]

        self.db.commit()
        return suggestion
