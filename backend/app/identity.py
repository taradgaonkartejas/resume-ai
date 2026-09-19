from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User


def current_user_id(
    db: Annotated[Session, Depends(get_db)],
    x_user_id: Annotated[Optional[str], Header(alias="X-User-Id")] = None,
    user_id: Annotated[Optional[str], Query()] = None,
) -> str:
    candidate = x_user_id or user_id
    if candidate:
        if not db.get(User, candidate):
            raise HTTPException(404, f"Unknown user: {candidate}")
        return candidate
    first = db.query(User).order_by(User.created_at).first()
    if not first:
        raise HTTPException(503, "No users seeded — run `make seed`")
    return first.id


CurrentUser = Annotated[str, Depends(current_user_id)]


def owned(query, model, user_id: str):
    """Every read of user-owned data goes through here."""
    return query.filter(model.user_id == user_id)