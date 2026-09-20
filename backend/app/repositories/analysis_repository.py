import uuid

from sqlalchemy import select

from app.models import AnalysisReport
from app.repositories.base import BaseRepository


class AnalysisRepository(BaseRepository):
    def latest_for_resume(self, resume_id: uuid.UUID) -> AnalysisReport | None:
        return self.db.scalar(
            select(AnalysisReport)
            .where(AnalysisReport.resume_id == resume_id)
            .order_by(AnalysisReport.created_at.desc())
            .limit(1)
        )

    def create(
        self,
        resume_id: uuid.UUID,
        overall_score: int,
        category_scores: dict,
        role_tags: list,
        trace: dict,
    ) -> AnalysisReport:
        report = AnalysisReport(
            resume_id=resume_id,
            overall_score=overall_score,
            category_scores=category_scores,
            role_tags=role_tags,
            trace=trace,
        )
        self.db.add(report)
        self.db.flush()
        return report
