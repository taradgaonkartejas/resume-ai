import uuid

from sqlalchemy import func, select

from app.models import Resume
from app.repositories.base import BaseRepository


class ResumeRepository(BaseRepository):
    """All reads are scoped by user_id. There is no get-by-id-only method."""

    def list_for_user(self, user_id: uuid.UUID) -> list[Resume]:
        return list(
            self.db.scalars(
                select(Resume)
                .where(Resume.user_id == user_id)
                .order_by(Resume.updated_at.desc())
            )
        )

    def get_owned(self, resume_id: uuid.UUID, user_id: uuid.UUID) -> Resume | None:
        return self.db.scalar(
            select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id)
        )

    def others_using_storage_key(self, storage_key: str, exclude_id: uuid.UUID) -> bool:
        """True when another resume still points at the same uploaded file.

        Forks share their parent's storage_key, so deletion must not remove an
        object another row still parses from.
        """
        return (
            self.db.scalar(
                select(Resume.id)
                .where(Resume.storage_key == storage_key, Resume.id != exclude_id)
                .limit(1)
            )
            is not None
        )

    def latest_scores(self, resume_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """resume_id -> newest overall_score, in ONE query.

        The library shows a strength ring per card; fetching the latest
        analysis per row would be an N+1 across the whole grid.
        """
        if not resume_ids:
            return {}
        from app.models import AnalysisReport

        newest = (
            select(
                AnalysisReport.resume_id,
                func.max(AnalysisReport.created_at).label("created_at"),
            )
            .where(AnalysisReport.resume_id.in_(resume_ids))
            .group_by(AnalysisReport.resume_id)
            .subquery()
        )
        rows = self.db.execute(
            select(AnalysisReport.resume_id, AnalysisReport.overall_score).join(
                newest,
                (AnalysisReport.resume_id == newest.c.resume_id)
                & (AnalysisReport.created_at == newest.c.created_at),
            )
        ).all()
        return {resume_id: score for resume_id, score in rows}

    def create(self, user_id: uuid.UUID, title: str, **kwargs) -> Resume:
        resume = Resume(user_id=user_id, title=title, **kwargs)
        self.db.add(resume)
        self.db.flush()
        return resume

    def orphan_children(self, parent_id: uuid.UUID) -> int:
        """Detach forks from a parent being deleted, keeping the forks."""
        children = list(
            self.db.scalars(select(Resume).where(Resume.parent_id == parent_id))
        )
        for child in children:
            child.parent_id = None
        return len(children)

    def delete(self, resume: Resume) -> None:
        self.db.delete(resume)
