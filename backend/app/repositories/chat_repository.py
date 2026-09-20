import uuid

from sqlalchemy import select

from app.models import ChatMessage
from app.repositories.base import BaseRepository


class ChatRepository(BaseRepository):
    def list_for_resume(
        self, resume_id: uuid.UUID, limit: int = 100
    ) -> list[ChatMessage]:
        rows = list(
            self.db.scalars(
                select(ChatMessage)
                .where(ChatMessage.resume_id == resume_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
            )
        )
        return list(reversed(rows))

    def append(
        self,
        resume_id: uuid.UUID,
        role: str,
        content: str,
        suggestion_id: uuid.UUID | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            resume_id=resume_id,
            role=role,
            content=content,
            suggestion_id=suggestion_id,
        )
        self.db.add(message)
        self.db.flush()
        return message
