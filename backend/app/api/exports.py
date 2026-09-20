import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import export_service
from app.identity import CurrentUser
from app.services.export_service import ExportService

router = APIRouter(tags=["exports"])


@router.get("/resumes/{resume_id}/export")
def export_resume(
    resume_id: uuid.UUID,
    user_id: CurrentUser,
    svc: Annotated[ExportService, Depends(export_service)],
    format: Annotated[str, Query(pattern="^(pdf|docx|txt)$")] = "pdf",
):
    # Stream the bytes rather than redirecting: presigned URLs do not exist
    # when storage has fallen back to local disk.
    payload, media_type, filename = svc.export(resume_id, user_id, format)
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
