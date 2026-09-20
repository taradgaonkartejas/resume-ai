import uuid

from sqlalchemy import delete, func, select

from app.models import ResumeVersion
from app.repositories.base import BaseRepository


class VersionRepository(BaseRepository):
    def list_for_resume(self, resume_id: uuid.UUID) -> list[ResumeVersion]:
        return list(
            self.db.scalars(
                select(ResumeVersion)
                .where(ResumeVersion.resume_id == resume_id)
                .order_by(ResumeVersion.seq)
            )
        )

    def get_by_seq(self, resume_id: uuid.UUID, seq: int) -> ResumeVersion | None:
        return self.db.scalar(
            select(ResumeVersion).where(
                ResumeVersion.resume_id == resume_id, ResumeVersion.seq == seq
            )
        )

    def max_seq(self, resume_id: uuid.UUID) -> int:
        value = self.db.scalar(
            select(func.max(ResumeVersion.seq)).where(
                ResumeVersion.resume_id == resume_id
            )
        )
        return int(value or 0)

    def append(
        self,
        resume_id: uuid.UUID,
        seq: int,
        snapshot: dict,
        change_source: str,
        label: str = "",
    ) -> ResumeVersion:
        version = ResumeVersion(
            resume_id=resume_id,
            seq=seq,
            snapshot=snapshot,
            change_source=change_source,
            label=label,
        )
        self.db.add(version)
        self.db.flush()
        return version

    def truncate_after(self, resume_id: uuid.UUID, seq: int) -> None:
        """Drop the redo branch: a new edit while behind head rewrites history."""
        self.db.execute(
            delete(ResumeVersion).where(
                ResumeVersion.resume_id == resume_id, ResumeVersion.seq > seq
            )
        )
