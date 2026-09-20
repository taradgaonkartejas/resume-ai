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

    def create(self, resume_id: uuid.UUID, **kwargs) -> Suggestion:
        suggestion = Suggestion(resume_id=resume_id, **kwargs)
        self.db.add(suggestion)
        self.db.flush()
        return suggestion
