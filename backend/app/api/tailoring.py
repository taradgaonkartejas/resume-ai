import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import suggestion_service, tailoring_service
from app.identity import CurrentUser
from app.schemas import SuggestionBuckets, TailorIn, TailorSessionOut
from app.services.suggestion_service import SuggestionService
from app.services.tailoring_service import TailoringService

router = APIRouter(tags=["tailoring"])

TailorSvc = Annotated[TailoringService, Depends(tailoring_service)]
SuggestSvc = Annotated[SuggestionService, Depends(suggestion_service)]


@router.post("/resumes/{resume_id}/tailor", response_model=TailorSessionOut, status_code=201)
def start_tailoring(
    resume_id: uuid.UUID, payload: TailorIn, user_id: CurrentUser, svc: TailorSvc
):
    return svc.start(resume_id, user_id, payload.jd_title, payload.jd_content)


@router.get("/resumes/{resume_id}/tailor", response_model=list[TailorSessionOut])
def list_sessions(resume_id: uuid.UUID, user_id: CurrentUser, svc: TailorSvc):
    return svc.list_sessions(resume_id, user_id)


@router.get(
    "/resumes/{resume_id}/tailor/{session_id}/suggestions",
    response_model=SuggestionBuckets,
)
def list_suggestions(
    resume_id: uuid.UUID,
    session_id: uuid.UUID,
    user_id: CurrentUser,
    svc: SuggestSvc,
):
    return svc.list_for_session(session_id, user_id)
