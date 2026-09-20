from sqlalchemy import select

from app.models import Template
from app.repositories.base import BaseRepository


class TemplateRepository(BaseRepository):
    def list_all(self) -> list[Template]:
        return list(self.db.scalars(select(Template).order_by(Template.name)))

    def get_by_key(self, key: str) -> Template | None:
        return self.db.scalar(select(Template).where(Template.key == key))
