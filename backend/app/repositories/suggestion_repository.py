import uuid

from sqlalchemy import select

from app.models import Resume, Suggestion
from app.repositories.base import BaseRepository


class SuggestionRepository(BaseRepository):
    def list_for_session(
        self, session_id: uuid.UUID, status: str | None = None
    ) -> list[Suggestion]:
        stmt = select(Suggestion).where(Suggestion.session_id == session_id)
        if status:
            stmt = stmt.where(Suggestion.status == status)
        return list(self.db.scalars(stmt.order_by(Suggestion.created_at)))

    def get_owned(
        self, suggestion_id: uuid.UUID, user_id: uuid.UUID
    ) -> Suggestion | None:
        """Ownership via the suggestion's resume."""
        return self.db.scalar(
            select(Suggestion)
            .join(Resume, Resume.id == Suggestion.resume_id)
            .where(Suggestion.id == suggestion_id, Resume.user_id == user_id)
        )

    def list_pending_for_resume(
        self, resume_id: uuid.UUID, session_id: uuid.UUID | None = None
    ) -> list[Suggestion]:
        """Pending suggestions for a resume, optionally one session's.

        `session_id=None` means EVERY pending suggestion on the resume,
        analysis-origin ones included -- not "the ones with a null session".
        Accept All in the analysis pane has to reach rows that were created
        with `session_id=None`, so the filter is applied only when a session
        is actually named.
        """
        stmt = select(Suggestion).where(
            Suggestion.resume_id == resume_id,
            Suggestion.status == "pending",
        )
        if session_id is not None:
            stmt = stmt.where(Suggestion.session_id == session_id)
        return list(self.db.scalars(stmt.order_by(Suggestion.created_at)))

    def pending_with_ref_prefix(
        self, resume_id: uuid.UUID, prefix: str
    ) -> list[Suggestion]:
        """Pending suggestions whose target_ref points into a section.

        Used to refuse a reorder while drafts are outstanding. Refs are
        positional (`exp_2.bullet_1` = third entry, second bullet), so moving
        an entry re-points every ref under it at someone else's text.
        """
        return list(
            self.db.scalars(
                select(Suggestion).where(
                    Suggestion.resume_id == resume_id,
                    Suggestion.status == "pending",
                    Suggestion.target_ref.startswith(prefix),
                )
            )
        )

    def create(self, resume_id: uuid.UUID, **kwargs) -> Suggestion:
        suggestion = Suggestion(resume_id=resume_id, **kwargs)
        self.db.add(suggestion)
        self.db.flush()
        return suggestion
