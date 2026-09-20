from app.models import Template
from app.repositories.template_repository import TemplateRepository
from app.services.exceptions import TemplateNotFound


class TemplateService:
    def __init__(self, templates: TemplateRepository) -> None:
        self.templates = templates

    def list_templates(self):
        return self.templates.list_all()

    def require(self, key: str) -> Template:
        template = self.templates.get_by_key(key)
        if template is None:
            raise TemplateNotFound(key)
        return template
