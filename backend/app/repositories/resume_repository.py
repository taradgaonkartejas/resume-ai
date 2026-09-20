import uuid

from sqlalchemy import select

from app.models import Resume
from app.repositories.base import BaseRepository


class ResumeRepository(BaseRepository):
    """All reads are scoped by user_id. There is no get-by-id-only method."""

    def list_for_user(self, user_id: uuid.UUID) -> list[Resume]:
        return list(
            self.db.scalars(
                select(Resume)
                .where(Resume.user_id == user_id)
                .order_by(Resume.updated_at.desc())
            )
        )

    def get_owned(self, resume_id: uuid.UUID, user_id: uuid.UUID) -> Resume | None:
        return self.db.scalar(
            select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id)
        )

    def create(self, user_id: uuid.UUID, title: str, **kwargs) -> Resume:
        resume = Resume(user_id=user_id, title=title, **kwargs)
        self.db.add(resume)
        self.db.flush()
        return resume

    def delete(self, resume: Resume) -> None:
        self.db.delete(resume)
