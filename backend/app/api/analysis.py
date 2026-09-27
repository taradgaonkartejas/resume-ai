import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import analysis_service
from app.identity import CurrentUser
from app.schemas import AnalysisOut, AnalysisStepsOut
from app.services.analysis_service import AnalysisService

router = APIRouter(tags=["analysis"])

Svc = Annotated[AnalysisService, Depends(analysis_service)]


@router.post("/resumes/{resume_id}/analyze", response_model=AnalysisOut, status_code=201)
def analyze(resume_id: uuid.UUID, user_id: CurrentUser, svc: Svc):
    return svc.analyze(resume_id, user_id)


@router.get("/resumes/{resume_id}/analysis", response_model=AnalysisOut)
def latest_analysis(resume_id: uuid.UUID, user_id: CurrentUser, svc: Svc):
    return svc.latest(resume_id, user_id)


@router.get("/resumes/{resume_id}/analysis/steps", response_model=AnalysisStepsOut)
def analysis_steps(resume_id: uuid.UUID, user_id: CurrentUser, svc: Svc):
    """Guided-editor steps, recomputed live from the current resume.

    Unlike GET /analysis this never 404s on "not analysed yet" — the guided
    flow is the thing that produces the first score, so it must work before
    any report exists.
    """
    return svc.steps(resume_id, user_id)
