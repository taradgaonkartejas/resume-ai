from app.models import JobDescription
from app.repositories.job_description_repository import JobDescriptionRepository
from app.services.exceptions import JobDescriptionNotFound


class JobDescriptionService:
    def __init__(self, jds: JobDescriptionRepository) -> None:
        self.jds = jds

    def sample(self) -> JobDescription:
        jd = self.jds.get_sample()
        if jd is None:
            raise JobDescriptionNotFound("No sample job description seeded")
        return jd
