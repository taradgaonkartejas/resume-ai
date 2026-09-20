import uuid

from app.repositories.agent_run_repository import AgentRunRepository


class AgentRunService:
    def __init__(self, runs: AgentRunRepository) -> None:
        self.runs = runs

    def list_runs(self, user_id: uuid.UUID, limit: int = 100):
        return self.runs.list_for_user(user_id, limit)
