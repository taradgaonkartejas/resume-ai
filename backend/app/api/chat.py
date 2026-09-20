import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import chat_service
from app.identity import CurrentUser
from app.schemas import ChatHistoryOut, ChatIn, ChatReplyOut
from app.services.chat_service import ChatService

router = APIRouter(tags=["chat"])

Svc = Annotated[ChatService, Depends(chat_service)]


@router.get("/resumes/{resume_id}/chat", response_model=ChatHistoryOut)
def chat_history(resume_id: uuid.UUID, user_id: CurrentUser, svc: Svc):
    return svc.history(resume_id, user_id)


@router.post("/resumes/{resume_id}/chat", response_model=ChatReplyOut, status_code=201)
def send_message(
    resume_id: uuid.UUID, payload: ChatIn, user_id: CurrentUser, svc: Svc
):
    return svc.send(resume_id, user_id, payload.content)
