from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import user_service
from app.schemas import UserOut
from app.services.user_service import UserService

router = APIRouter(tags=["users"])


@router.get("/users", response_model=list[UserOut])
def list_users(svc: Annotated[UserService, Depends(user_service)]):
    return svc.list_users()
