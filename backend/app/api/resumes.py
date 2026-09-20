import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.api.deps import parsing_service, resume_service
from app.identity import CurrentUser
from app.schemas import (
    DeleteOut,
    ParseStatusOut,
    ResumeDataIn,
    ResumeOut,
    ResumeSummaryOut,
    TemplateIn,
)
from app.services.parsing import ParsingService
from app.services.resume_service import ResumeService

router = APIRouter(tags=["resumes"])

ResumeSvc = Annotated[ResumeService, Depends(resume_service)]
ParseSvc = Annotated[ParsingService, Depends(parsing_service)]


@router.get("/resumes", response_model=list[ResumeSummaryOut])
def list_resumes(user_id: CurrentUser, svc: ResumeSvc):
    return svc.list_resumes(user_id)


@router.post("/resumes/upload", response_model=ResumeOut, status_code=201)
async def upload_resume(
    user_id: CurrentUser,
    svc: ResumeSvc,
    parser: ParseSvc,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()] = "",
):
    content = await file.read()
    resume = svc.create_from_upload(user_id, title, file.filename or "resume.txt", content)
    # Parse inline: a local 5-user app does not need a job queue.
    return parser.parse_resume(resume.id, user_id)


@router.get("/resumes/{resume_id}", response_model=ResumeOut)
def get_resume(resume_id: uuid.UUID, user_id: CurrentUser, svc: ResumeSvc):
    return svc.get(resume_id, user_id)


@router.get("/resumes/{resume_id}/parse-status", response_model=ParseStatusOut)
def parse_status(resume_id: uuid.UUID, user_id: CurrentUser, svc: ResumeSvc):
    return svc.parse_status(resume_id, user_id)


@router.put("/resumes/{resume_id}/data", response_model=ResumeOut)
def update_data(
    resume_id: uuid.UUID, payload: ResumeDataIn, user_id: CurrentUser, svc: ResumeSvc
):
    return svc.update_data(resume_id, user_id, payload.structured_data)


@router.post("/resumes/{resume_id}/template", response_model=ResumeOut)
def apply_template(
    resume_id: uuid.UUID, payload: TemplateIn, user_id: CurrentUser, svc: ResumeSvc
):
    return svc.apply_template(resume_id, user_id, payload.template_key)


@router.delete("/resumes/{resume_id}", response_model=DeleteOut)
def delete_resume(resume_id: uuid.UUID, user_id: CurrentUser, svc: ResumeSvc):
    return svc.delete(resume_id, user_id)
