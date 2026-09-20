import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import version_service
from app.identity import CurrentUser
from app.schemas import ResumeOut, VersionOut
from app.services.version_service import VersionService

router = APIRouter(tags=["versions"])

Svc = Annotated[VersionService, Depends(version_service)]


@router.get("/resumes/{resume_id}/versions", response_model=list[VersionOut])
def list_versions(resume_id: uuid.UUID, user_id: CurrentUser, svc: Svc):
    return svc.list_versions(resume_id, user_id)


@router.post("/resumes/{resume_id}/undo", response_model=ResumeOut)
def undo(resume_id: uuid.UUID, user_id: CurrentUser, svc: Svc):
    return svc.undo(resume_id, user_id)


@router.post("/resumes/{resume_id}/redo", response_model=ResumeOut)
def redo(resume_id: uuid.UUID, user_id: CurrentUser, svc: Svc):
    return svc.redo(resume_id, user_id)
