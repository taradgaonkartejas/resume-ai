import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import suggestion_service
from app.identity import CurrentUser
from app.schemas import (
    AcceptAllIn,
    AcceptAllOut,
    RewriteRequest,
    SuggestionAction,
    SuggestionAnswer,
    SuggestionOut,
)
from app.services.suggestion_service import SuggestionService

router = APIRouter(tags=["suggestions"])


@router.get("/suggestions/{suggestion_id}", response_model=SuggestionOut)
def get_suggestion(
    suggestion_id: uuid.UUID,
    user_id: CurrentUser,
    svc: Annotated[SuggestionService, Depends(suggestion_service)],
):
    """One card by id, scoped to its owner.

    The chat pane stores only a `suggestion_id` against a message, so on
    reload it cannot tell an ordinary draft from a question card -- and
    offering Accept on a question raises, which left the skills picker
    unreachable from chat. Status is live state, so it is read, not cached
    into the transcript.
    """
    return svc.get(suggestion_id, user_id)


@router.patch("/suggestions/{suggestion_id}", response_model=SuggestionOut)
def act_on_suggestion(
    suggestion_id: uuid.UUID,
    payload: SuggestionAction,
    user_id: CurrentUser,
    svc: Annotated[SuggestionService, Depends(suggestion_service)],
):
    return svc.act(suggestion_id, user_id, payload.action, payload.edited_text)


@router.post("/suggestions/{suggestion_id}/answer", response_model=SuggestionOut)
def answer_question(
    suggestion_id: uuid.UUID,
    payload: SuggestionAnswer,
    user_id: CurrentUser,
    svc: Annotated[SuggestionService, Depends(suggestion_service)],
):
    """Supply the fact a clarifying question asked for.

    Returns 200 with the card either moved to `pending` (attested) or to
    `rejected` (declined) -- declining is a normal outcome, not an error, so
    it is not a 4xx. 422 is reserved for a yes with no text on a question
    that needs text.
    """
    return svc.answer_question(
        suggestion_id, user_id, payload.answer, payload.confirmed
    )


@router.post("/resumes/{resume_id}/suggestions/accept-all", response_model=AcceptAllOut)
def accept_all(
    resume_id: uuid.UUID,
    user_id: CurrentUser,
    svc: Annotated[SuggestionService, Depends(suggestion_service)],
    payload: AcceptAllIn | None = None,
):
    """Apply every applicable pending suggestion as ONE version.

    One undo reverts the whole batch. Suggestions that are stale or waiting
    on an answer are skipped and named in the response rather than forced --
    the button saves clicks on the uncontroversial ones, it does not
    overrule the checks that stop a patch landing on text it was not
    written against.
    """
    return svc.accept_all(
        resume_id, user_id, payload.session_id if payload else None
    )


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
