import uuid

from app.config import settings
from app.models import Resume
from app.repositories.resume_repository import ResumeRepository
from app.repositories.template_repository import TemplateRepository
from app.repositories.vector_repository import VectorRepository
from app.services import storage
from app.services.exceptions import (
    ResumeNotFound,
    TemplateNotFound,
    UnsupportedFormat,
    ValidationError,
)
from app.services.resume_ops import empty_resume
from app.services.version_service import VersionService

ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt"}


class ResumeService:
    def __init__(
        self,
        resumes: ResumeRepository,
        templates: TemplateRepository,
        vectors: VectorRepository,
        versions: VersionService,
    ) -> None:
        self.resumes = resumes
        self.templates = templates
        self.vectors = vectors
        self.versions = versions

    @property
    def db(self):
        return self.resumes.db

    def list_resumes(self, user_id: uuid.UUID):
        return self.resumes.list_for_user(user_id)

    def get(self, resume_id: uuid.UUID, user_id: uuid.UUID) -> Resume:
        resume = self.resumes.get_owned(resume_id, user_id)
        if resume is None:
            raise ResumeNotFound(str(resume_id))
        return resume

    def create_from_upload(
        self,
        user_id: uuid.UUID,
        title: str,
        filename: str,
        content: bytes,
    ) -> Resume:
        suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if suffix not in ALLOWED_SUFFIXES:
            raise UnsupportedFormat(
                f"{suffix or filename}: expected one of {sorted(ALLOWED_SUFFIXES)}"
            )
        max_bytes = settings.max_upload_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise ValidationError(f"File exceeds {settings.max_upload_mb} MB")

        resume = self.resumes.create(
            user_id=user_id,
            title=title or filename,
            structured_data=empty_resume(),
            parse_status="pending",
        )
        key = storage.object_key(user_id, resume.id, filename)
        storage.put_object(settings.s3_bucket_uploads, key, content)
        resume.storage_key = key
        self.db.commit()
        return resume

    def create_blank(self, user_id: uuid.UUID, title: str) -> Resume:
        resume = self.resumes.create(
            user_id=user_id,
            title=title,
            structured_data=empty_resume(),
            parse_status="ready",
        )
        self.versions.record(resume, resume.structured_data, "created", "Created")
        self.db.commit()
        return resume

    def update_data(
        self, resume_id: uuid.UUID, user_id: uuid.UUID, structured_data: dict
    ) -> Resume:
        resume = self.get(resume_id, user_id)
        resume.structured_data = structured_data
        self.versions.record(resume, structured_data, "manual", "Manual edit")
        self.db.commit()
        return resume

    def apply_template(
        self, resume_id: uuid.UUID, user_id: uuid.UUID, template_key: str
    ) -> Resume:
        resume = self.get(resume_id, user_id)
        if self.templates.get_by_key(template_key) is None:
            raise TemplateNotFound(template_key)
        resume.template_key = template_key
        self.db.commit()
        return resume

    def delete(self, resume_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        """Purge DB rows, vectors and stored objects.

        Orphaned vectors are the failure that matters: they keep surfacing in
        retrieval for a resume the user believes is gone.
        """
        resume = self.get(resume_id, user_id)
        vectors_removed = self.vectors.delete_for_resume(resume_id, user_id)
        prefix = f"{user_id}/{resume_id}/"
        objects_removed = storage.delete_prefix(settings.s3_bucket_uploads, prefix)
        objects_removed += storage.delete_prefix(settings.s3_bucket_exports, prefix)
        self.resumes.delete(resume)
        self.db.commit()
        return {
            "deleted": str(resume_id),
            "vectors_removed": vectors_removed,
            "objects_removed": objects_removed,
        }

    def parse_status(self, resume_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        resume = self.get(resume_id, user_id)
        return {
            "resume_id": str(resume.id),
            "parse_status": resume.parse_status,
            "parse_note": resume.parse_note,
        }
