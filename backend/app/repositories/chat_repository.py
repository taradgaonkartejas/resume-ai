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

    def recent_turns(
        self, resume_id: uuid.UUID, limit: int = 10
    ) -> list[ChatMessage]:
        """The last few turns of actual conversation, oldest first.

        `event` rows are excluded deliberately. They are a log of what
        happened in the Analyze/Tailor panes, not things anyone said; feeding
        "Applied to summary.text." back in as dialogue would invite the model
        to narrate the user's own edits back at them, which is the authorship
        claim the chat pane exists to avoid.
        """
        rows = list(
            self.db.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.resume_id == resume_id,
                    ChatMessage.role.in_(("user", "assistant")),
                )
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
        meta: dict | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            resume_id=resume_id,
            role=role,
            content=content,
            suggestion_id=suggestion_id,
            meta=meta or {},
        )
        self.db.add(message)
        self.db.flush()
        return message

    def append_event(
        self,
        resume_id: uuid.UUID,
        kind: str,
        content: str,
        suggestion_id: uuid.UUID | None = None,
        **fields,
    ) -> ChatMessage:
        """Record something that happened in another pane.

        Separate from `append` so the two cannot be confused at a call site:
        an event must never be written with role "assistant". ChatPane's
        standing rule is that the assistant never claims to have edited the
        resume, and an event that borrowed that role would do exactly that.

        Deliberately does NOT touch the user's chat token budget. Events are
        not model calls -- billing a button click against a 25-message
        allowance would let the Analyze pane silently consume the chat.
        """
        return self.append(
            resume_id,
            "event",
            content,
            suggestion_id=suggestion_id,
            meta={"kind": kind, **fields},
        )
