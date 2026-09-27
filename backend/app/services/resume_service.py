import logging
import uuid
from copy import deepcopy

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
from app.services.resume_ops import empty_resume, migrate
from app.services.version_service import VersionService

logger = logging.getLogger(__name__)

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

    def list_resumes(self, user_id: uuid.UUID) -> list[dict]:
        """Library rows, including each resume's latest strength score.

        Returns plain dicts rather than ORM objects because overall_score is
        not a column. Scores come from ONE grouped query, not one per card.
        """
        resumes = self.resumes.list_for_user(user_id)
        scores = self.resumes.latest_scores([r.id for r in resumes])
        return [
            {
                "id": r.id,
                "title": r.title,
                "template_key": r.template_key,
                "parse_status": r.parse_status,
                "kind": r.kind,
                "parent_id": r.parent_id,
                "tailored_for": r.tailored_for,
                "version_cursor": r.version_cursor,
                "updated_at": r.updated_at,
                "overall_score": scores.get(r.id),
                "structured_data": r.structured_data or {},
            }
            for r in resumes
        ]

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

    def fork(
        self,
        resume_id: uuid.UUID,
        user_id: uuid.UUID,
        title: str = "",
        tailored_for: str = "",
        kind: str = "tailored",
    ) -> Resume:
        """Copy a resume into a new row so a base and its variants coexist.

        Tailoring used to mutate the single resume in place, which made it
        impossible to keep a pristine base alongside per-job versions.

        Copied: structured_data, template_key, raw_text, storage_key.
        NOT copied: versions and analyses -- a fork is a new document and
        starts its own history, so undo cannot reach across into the parent.

        The vector copy is load-bearing, not an optimisation. The writer agent
        retrieves grounding bullets scoped to resume_id, so a fork with no
        vectors would silently produce weaker, ungrounded suggestions with no
        error at all.
        """
        source = self.get(resume_id, user_id)

        child = self.resumes.create(
            user_id=user_id,
            title=title or f"{source.title} (copy)",
            structured_data=deepcopy(source.structured_data or {}),
            raw_text=source.raw_text,
            # Both rows point at the same uploaded object. Deleting one must
            # not delete the other's file -- see delete() below.
            storage_key=source.storage_key,
            parse_status=source.parse_status,
            parse_note=source.parse_note,
            template_key=source.template_key,
            kind=kind,
            parent_id=source.id,
            tailored_for=tailored_for,
        )

        self.versions.record(child, child.structured_data, "created", "Forked from base")
        self._index(child)
        self.db.commit()
        return child

    def rename(self, resume_id: uuid.UUID, user_id: uuid.UUID, title: str) -> Resume:
        title = (title or "").strip()
        if not title:
            raise ValidationError("Title cannot be empty")
        resume = self.get(resume_id, user_id)
        resume.title = title[:200]
        self.db.commit()
        return resume

    def _index(self, resume: Resume) -> int:
        """Embed the resume's bullets for grounding retrieval.

        Indexing was previously only ever done by the seeder, so every
        UPLOADED resume had zero vectors and the writer agent fell back to
        thin context. Called on parse and on fork.

        Never fatal: embedding may reach the network, and a resume you can
        edit and export is far more valuable than one that failed to save
        because an embedding call timed out.
        """
        if self.vectors is None:
            return 0
        try:
            from app.ai.vectorstore import VectorStore

            return VectorStore(self.vectors).index_resume(
                resume.user_id, resume.id, resume.structured_data or {}
            )
        except Exception as exc:  # noqa: BLE001 -- degraded retrieval, not a failed request
            logger.warning("Indexing resume %s failed: %s", resume.id, exc)
            return 0

    def update_data(
        self, resume_id: uuid.UUID, user_id: uuid.UUID, structured_data: dict
    ) -> Resume:
        resume = self.get(resume_id, user_id)
        # Normalise on write: the editor sends start/end/current and the
        # derived `dates` mirror is refreshed here, so no caller can persist a
        # document whose rendered dates disagree with its structured ones.
        structured_data = migrate(structured_data)
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

        # Exports are always keyed by THIS resume's id, so they are safe to
        # purge. The uploaded source file is not: a fork shares its parent's
        # storage_key, so deleting the child by prefix would destroy the file
        # the parent still parses from. Only remove the upload when no other
        # resume references it.
        prefix = f"{user_id}/{resume_id}/"
        objects_removed = storage.delete_prefix(settings.s3_bucket_exports, prefix)
        if resume.storage_key and not self.resumes.others_using_storage_key(
            resume.storage_key, resume.id
        ):
            objects_removed += storage.delete_prefix(settings.s3_bucket_uploads, prefix)

        # Orphan the children explicitly instead of trusting ON DELETE SET
        # NULL: SQLite does not enforce foreign-key actions unless
        # PRAGMA foreign_keys is on, so relying on the database would make
        # this behaviour differ between SQLite and Postgres.
        orphaned = self.resumes.orphan_children(resume.id)
        self.resumes.delete(resume)
        self.db.commit()
        return {
            "deleted": str(resume_id),
            "vectors_removed": vectors_removed,
            "objects_removed": objects_removed,
            "children_orphaned": orphaned,
        }

    def parse_status(self, resume_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        resume = self.get(resume_id, user_id)
        return {
            "resume_id": str(resume.id),
            "parse_status": resume.parse_status,
            "parse_note": resume.parse_note,
        }
