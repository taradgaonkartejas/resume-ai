import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import suggestion_service
from app.identity import CurrentUser
from app.schemas import SuggestionAction, SuggestionOut
from app.services.suggestion_service import SuggestionService

router = APIRouter(tags=["suggestions"])


@router.patch("/suggestions/{suggestion_id}", response_model=SuggestionOut)
def act_on_suggestion(
    suggestion_id: uuid.UUID,
    payload: SuggestionAction,
    user_id: CurrentUser,
    svc: Annotated[SuggestionService, Depends(suggestion_service)],
):
    return svc.act(suggestion_id, user_id, payload.action, payload.edited_text)
