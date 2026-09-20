import uuid

from sqlalchemy import select

from app.models import AgentRun
from app.repositories.base import BaseRepository


class AgentRunRepository(BaseRepository):
    def list_for_user(self, user_id: uuid.UUID, limit: int = 100) -> list[AgentRun]:
        return list(
            self.db.scalars(
                select(AgentRun)
                .where(AgentRun.user_id == user_id)
                .order_by(AgentRun.created_at.desc())
                .limit(limit)
            )
        )

    def record(
        self,
        user_id: uuid.UUID,
        agent: str,
        task: str = "",
        model: str = "",
        status: str = "ok",
        latency_ms: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        thread_id: str = "",
    ) -> AgentRun:
        run = AgentRun(
            user_id=user_id,
            agent=agent,
            task=task,
            model=model,
            status=status,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            thread_id=thread_id,
        )
        self.db.add(run)
        self.db.flush()
        return run
