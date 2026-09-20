import uuid

from app.models import Resume
from app.repositories.resume_repository import ResumeRepository
from app.repositories.version_repository import VersionRepository
from app.services.exceptions import InvalidStateTransition, ResumeNotFound


class VersionService:
    """Owns the version cursor.

    Undo/redo move the cursor; they never delete rows. A new edit made while
    the cursor is behind head truncates the forward (redo) branch.
    """

    def __init__(self, versions: VersionRepository, resumes: ResumeRepository) -> None:
        self.versions = versions
        self.resumes = resumes

    def _owned(self, resume_id: uuid.UUID, user_id: uuid.UUID) -> Resume:
        resume = self.resumes.get_owned(resume_id, user_id)
        if resume is None:
            raise ResumeNotFound(str(resume_id))
        return resume

    def list_versions(self, resume_id: uuid.UUID, user_id: uuid.UUID):
        self._owned(resume_id, user_id)
        return self.versions.list_for_resume(resume_id)

    def record(
        self,
        resume: Resume,
        snapshot: dict,
        change_source: str,
        label: str = "",
    ):
        """Append a version at cursor+1, dropping any redo branch first.

        Caller is responsible for committing.
        """
        cursor = resume.version_cursor
        if cursor < self.versions.max_seq(resume.id):
            self.versions.truncate_after(resume.id, cursor)
        next_seq = cursor + 1
        version = self.versions.append(
            resume.id, next_seq, snapshot, change_source, label
        )
        resume.version_cursor = next_seq
        return version

    def undo(self, resume_id: uuid.UUID, user_id: uuid.UUID) -> Resume:
        resume = self._owned(resume_id, user_id)
        target = resume.version_cursor - 1
        if target < 1:
            raise InvalidStateTransition("Nothing to undo")
        version = self.versions.get_by_seq(resume_id, target)
        if version is None:
            raise InvalidStateTransition("Nothing to undo")
        resume.structured_data = version.snapshot
        resume.version_cursor = target
        self.resumes.db.commit()
        return resume

    def redo(self, resume_id: uuid.UUID, user_id: uuid.UUID) -> Resume:
        resume = self._owned(resume_id, user_id)
        target = resume.version_cursor + 1
        version = self.versions.get_by_seq(resume_id, target)
        if version is None:
            raise InvalidStateTransition("Nothing to redo")
        resume.structured_data = version.snapshot
        resume.version_cursor = target
        self.resumes.db.commit()
        return resume
