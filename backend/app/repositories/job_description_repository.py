import uuid

from sqlalchemy import select

from app.models import JobDescription
from app.repositories.base import BaseRepository


class JobDescriptionRepository(BaseRepository):
    def get(self, jd_id: uuid.UUID) -> JobDescription | None:
        return self.db.get(JobDescription, jd_id)

    def get_sample(self) -> JobDescription | None:
        return self.db.scalar(
            select(JobDescription).where(JobDescription.is_sample.is_(True)).limit(1)
        )

    def create(
        self, title: str, content: str, extracted_keywords: list | None = None
    ) -> JobDescription:
        jd = JobDescription(
            title=title,
            content=content,
            extracted_keywords=extracted_keywords or [],
        )
        self.db.add(jd)
        self.db.flush()
        return jd
