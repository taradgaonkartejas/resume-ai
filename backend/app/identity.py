import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User


def current_user_id(
    db: Annotated[Session, Depends(get_db)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    user_id: Annotated[str | None, Query()] = None,
) -> uuid.UUID:
    """No auth: the client declares who it is.

    Resolution order: X-User-Id header -> ?user_id= -> first seeded user.
    """
    candidate = x_user_id or user_id
    if candidate:
        try:
            parsed = uuid.UUID(str(candidate))
        except (ValueError, AttributeError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="Malformed user id") from exc
        if db.get(User, parsed) is None:
            raise HTTPException(status_code=404, detail=f"Unknown user: {candidate}")
        return parsed

    first = db.scalar(select(User).order_by(User.created_at))
    if first is None:
        raise HTTPException(status_code=503, detail="No users seeded — run `make seed`")
    return first.id


CurrentUser = Annotated[uuid.UUID, Depends(current_user_id)]
