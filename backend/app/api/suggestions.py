import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import suggestion_service
from app.identity import CurrentUser
from app.schemas import RewriteRequest, SuggestionAction, SuggestionOut
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


@router.post(
    "/resumes/{resume_id}/sections/{target_ref}/rewrite",
    response_model=SuggestionOut,
    status_code=201,
)
def rewrite_section(
    resume_id: uuid.UUID,
    target_ref: str,
    user_id: CurrentUser,
    svc: Annotated[SuggestionService, Depends(suggestion_service)],
    payload: RewriteRequest | None = None,
):
    """Draft an AI fix for one finding, as an ordinary pending Suggestion.

    target_ref contains dots ("exp_0.bullet_0") but never slashes, so the
    default path converter handles it; no :path converter is needed, and using
    one would swallow the trailing "/rewrite".
    """
    body = payload or RewriteRequest()
    return svc.rewrite_section(
        resume_id,
        user_id,
        target_ref,
        finding_id=body.finding_id,
        regenerate=body.regenerate,
    )
