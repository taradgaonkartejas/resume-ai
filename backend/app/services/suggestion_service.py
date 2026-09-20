import uuid

from app.models import Suggestion
from app.repositories.resume_repository import ResumeRepository
from app.repositories.suggestion_repository import SuggestionRepository
from app.repositories.tailoring_repository import TailoringRepository
from app.services import resume_ops
from app.services.exceptions import (
    InvalidStateTransition,
    SessionNotFound,
    SuggestionNotFound,
    ValidationError,
)
from app.services.heuristics import match_keywords
from app.services.version_service import VersionService

TERMINAL = {"accepted", "rejected"}


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
