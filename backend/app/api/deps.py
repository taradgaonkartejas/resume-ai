from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.identity import CurrentUser  # noqa: F401  re-exported for controllers
from app.repositories.agent_run_repository import AgentRunRepository
from app.repositories.analysis_repository import AnalysisRepository
from app.repositories.chat_repository import ChatRepository
from app.repositories.job_description_repository import JobDescriptionRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.suggestion_repository import SuggestionRepository
from app.repositories.tailoring_repository import TailoringRepository
from app.repositories.template_repository import TemplateRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vector_repository import VectorRepository
from app.repositories.version_repository import VersionRepository
from app.services.agent_run_service import AgentRunService
from app.services.analysis_service import AnalysisService
from app.services.chat_service import ChatService
from app.services.export_service import ExportService
from app.services.job_description_service import JobDescriptionService
from app.services.parsing import ParsingService
from app.services.resume_service import ResumeService
from app.services.suggestion_service import SuggestionService
from app.services.tailoring_service import TailoringService
from app.services.template_service import TemplateService
from app.services.user_service import UserService
from app.services.version_service import VersionService

DbSession = Annotated[Session, Depends(get_db)]


# ---------- repositories (one Session per request, shared by all) ----------
def user_repo(db: DbSession) -> UserRepository:
    return UserRepository(db)


def template_repo(db: DbSession) -> TemplateRepository:
    return TemplateRepository(db)


def resume_repo(db: DbSession) -> ResumeRepository:
    return ResumeRepository(db)


def version_repo(db: DbSession) -> VersionRepository:
    return VersionRepository(db)


def analysis_repo(db: DbSession) -> AnalysisRepository:
    return AnalysisRepository(db)


def jd_repo(db: DbSession) -> JobDescriptionRepository:
    return JobDescriptionRepository(db)


def tailoring_repo(db: DbSession) -> TailoringRepository:
    return TailoringRepository(db)


def suggestion_repo(db: DbSession) -> SuggestionRepository:
    return SuggestionRepository(db)


def chat_repo(db: DbSession) -> ChatRepository:
    return ChatRepository(db)


def vector_repo(db: DbSession) -> VectorRepository:
    return VectorRepository(db)


def agent_run_repo(db: DbSession) -> AgentRunRepository:
    return AgentRunRepository(db)


# ---------- services ----------
def version_service(
    versions: Annotated[VersionRepository, Depends(version_repo)],
    resumes: Annotated[ResumeRepository, Depends(resume_repo)],
) -> VersionService:
    return VersionService(versions, resumes)


def user_service(
    users: Annotated[UserRepository, Depends(user_repo)],
) -> UserService:
    return UserService(users)


def template_service(
    templates: Annotated[TemplateRepository, Depends(template_repo)],
) -> TemplateService:
    return TemplateService(templates)


def resume_service(
    resumes: Annotated[ResumeRepository, Depends(resume_repo)],
    templates: Annotated[TemplateRepository, Depends(template_repo)],
    vectors: Annotated[VectorRepository, Depends(vector_repo)],
    versions: Annotated[VersionService, Depends(version_service)],
) -> ResumeService:
    return ResumeService(resumes, templates, vectors, versions)


def parsing_service(
    resumes: Annotated[ResumeRepository, Depends(resume_repo)],
) -> ParsingService:
    return ParsingService(resumes)


def analysis_service(
    analyses: Annotated[AnalysisRepository, Depends(analysis_repo)],
    resumes: Annotated[ResumeRepository, Depends(resume_repo)],
    vectors: Annotated[VectorRepository, Depends(vector_repo)],
    runs: Annotated[AgentRunRepository, Depends(agent_run_repo)],
) -> AnalysisService:
    return AnalysisService(analyses, resumes, vectors, runs)


def tailoring_service(
    sessions: Annotated[TailoringRepository, Depends(tailoring_repo)],
    suggestions: Annotated[SuggestionRepository, Depends(suggestion_repo)],
    resumes: Annotated[ResumeRepository, Depends(resume_repo)],
    jds: Annotated[JobDescriptionRepository, Depends(jd_repo)],
    vectors: Annotated[VectorRepository, Depends(vector_repo)],
    runs: Annotated[AgentRunRepository, Depends(agent_run_repo)],
) -> TailoringService:
    return TailoringService(sessions, suggestions, resumes, jds, vectors, runs)


def suggestion_service(
    suggestions: Annotated[SuggestionRepository, Depends(suggestion_repo)],
    resumes: Annotated[ResumeRepository, Depends(resume_repo)],
    sessions: Annotated[TailoringRepository, Depends(tailoring_repo)],
    versions: Annotated[VersionService, Depends(version_service)],
) -> SuggestionService:
    return SuggestionService(suggestions, resumes, sessions, versions)


def chat_service(
    chats: Annotated[ChatRepository, Depends(chat_repo)],
    resumes: Annotated[ResumeRepository, Depends(resume_repo)],
    suggestions: Annotated[SuggestionRepository, Depends(suggestion_repo)],
    users: Annotated[UserRepository, Depends(user_repo)],
    vectors: Annotated[VectorRepository, Depends(vector_repo)],
    runs: Annotated[AgentRunRepository, Depends(agent_run_repo)],
) -> ChatService:
    return ChatService(chats, resumes, suggestions, users, vectors, runs)


def export_service(
    resumes: Annotated[ResumeRepository, Depends(resume_repo)],
) -> ExportService:
    return ExportService(resumes)


def jd_service(
    jds: Annotated[JobDescriptionRepository, Depends(jd_repo)],
) -> JobDescriptionService:
    return JobDescriptionService(jds)


def agent_run_service(
    runs: Annotated[AgentRunRepository, Depends(agent_run_repo)],
) -> AgentRunService:
    return AgentRunService(runs)
