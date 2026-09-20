from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import agent_run_service, jd_service
from app.identity import CurrentUser
from app.schemas import AgentRunOut, JobDescriptionOut
from app.services.agent_run_service import AgentRunService
from app.services.job_description_service import JobDescriptionService

router = APIRouter(tags=["admin"])


@router.get("/sample-jd", response_model=JobDescriptionOut)
def sample_jd(svc: Annotated[JobDescriptionService, Depends(jd_service)]):
    return svc.sample()


@router.get("/admin/agent-runs", response_model=list[AgentRunOut])
def agent_runs(
    user_id: CurrentUser,
    svc: Annotated[AgentRunService, Depends(agent_run_service)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    return svc.list_runs(user_id, limit)
