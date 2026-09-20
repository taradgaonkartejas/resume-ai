import uuid

from sqlalchemy import select

from app.models import Resume, TailoringSession
from app.repositories.base import BaseRepository


class TailoringRepository(BaseRepository):
    def list_for_resume(self, resume_id: uuid.UUID) -> list[TailoringSession]:
        return list(
            self.db.scalars(
                select(TailoringSession)
                .where(TailoringSession.resume_id == resume_id)
                .order_by(TailoringSession.created_at.desc())
            )
        )

    def get_owned(
        self, session_id: uuid.UUID, user_id: uuid.UUID
    ) -> TailoringSession | None:
        """Ownership is one hop up: session -> resume -> user."""
        return self.db.scalar(
            select(TailoringSession)
            .join(Resume, Resume.id == TailoringSession.resume_id)
            .where(TailoringSession.id == session_id, Resume.user_id == user_id)
        )

    def create(
        self,
        resume_id: uuid.UUID,
        job_description_id: uuid.UUID,
        thread_id: str,
        **kwargs,
    ) -> TailoringSession:
        session = TailoringSession(
            resume_id=resume_id,
            job_description_id=job_description_id,
            thread_id=thread_id,
            **kwargs,
        )
        self.db.add(session)
        self.db.flush()
        return session
