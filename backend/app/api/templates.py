from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import template_service
from app.schemas import TemplateOut
from app.services.template_service import TemplateService

router = APIRouter(tags=["templates"])


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(svc: Annotated[TemplateService, Depends(template_service)]):
    return svc.list_templates()
