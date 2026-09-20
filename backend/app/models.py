import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.db import Base

# JSONB on Postgres, JSON on SQLite — one model, both backends.
JSONType = JSONB().with_variant(JSON(), "sqlite")
# VECTOR(n) on Postgres (indexable by ivfflat/hnsw), JSON list on SQLite.
EmbeddingType = Vector(settings.embedding_dim).with_variant(JSON(), "sqlite")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(120), default="")
    avatar_color: Mapped[str] = mapped_column(String(20), default="#2563eb")
    chat_tokens_left: Mapped[int] = mapped_column(Integer, default=25)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    resumes: Mapped[list["Resume"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(300), default="")
    design_tokens: Mapped[dict] = mapped_column(JSONType, default=dict)


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    structured_data: Mapped[dict] = mapped_column(JSONType, default=dict)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    storage_key: Mapped[str] = mapped_column(String(400), default="")
    parse_status: Mapped[str] = mapped_column(String(20), default="pending")
    parse_note: Mapped[str] = mapped_column(Text, default="")
    template_key: Mapped[str] = mapped_column(
        String(40), ForeignKey("templates.key"), default="modern"
    )
    version_cursor: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    user: Mapped["User"] = relationship(back_populates="resumes")
    versions: Mapped[list["ResumeVersion"]] = relationship(
        back_populates="resume", cascade="all, delete-orphan"
    )
    analyses: Mapped[list["AnalysisReport"]] = relationship(
        back_populates="resume", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["TailoringSession"]] = relationship(
        back_populates="resume", cascade="all, delete-orphan"
    )
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="resume", cascade="all, delete-orphan"
    )


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    resume_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resumes.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONType, default=dict)
    change_source: Mapped[str] = mapped_column(String(40), default="manual")
    label: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    resume: Mapped["Resume"] = relationship(back_populates="versions")

    __table_args__ = (
        Index("ux_resume_versions_seq", "resume_id", "seq", unique=True),
    )


class AnalysisReport(Base):
    __tablename__ = "analysis_reports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    resume_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resumes.id", ondelete="CASCADE"), index=True
    )
    overall_score: Mapped[int] = mapped_column(Integer, default=0)
    category_scores: Mapped[dict] = mapped_column(JSONType, default=dict)
    role_tags: Mapped[list] = mapped_column(JSONType, default=list)
    trace: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    resume: Mapped["Resume"] = relationship(back_populates="analyses")


class JobDescription(Base):
    __tablename__ = "job_descriptions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    extracted_keywords: Mapped[list] = mapped_column(JSONType, default=list)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TailoringSession(Base):
    __tablename__ = "tailoring_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    resume_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resumes.id", ondelete="CASCADE"), index=True
    )
    job_description_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("job_descriptions.id", ondelete="CASCADE")
    )
    match_percent: Mapped[float] = mapped_column(Float, default=0.0)
    baseline_percent: Mapped[float] = mapped_column(Float, default=0.0)
    matched_keywords: Mapped[list] = mapped_column(JSONType, default=list)
    gap_keywords: Mapped[list] = mapped_column(JSONType, default=list)
    all_keywords: Mapped[list] = mapped_column(JSONType, default=list)
    thread_id: Mapped[str] = mapped_column(String(80), default="")
    graph_state: Mapped[str] = mapped_column(String(40), default="created")
    trace: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    resume: Mapped["Resume"] = relationship(back_populates="sessions")
    job_description: Mapped["JobDescription"] = relationship()
    suggestions: Mapped[list["Suggestion"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class Suggestion(Base):
    __tablename__ = "suggestions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tailoring_sessions.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    resume_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resumes.id", ondelete="CASCADE"), index=True
    )
    origin: Mapped[str] = mapped_column(String(20), default="tailor")
    section: Mapped[str] = mapped_column(String(40), default="")
    target_ref: Mapped[str] = mapped_column(String(80), default="")
    placement: Mapped[str] = mapped_column(String(200), default="")
    original_text: Mapped[str] = mapped_column(Text, default="")
    suggested_text: Mapped[str] = mapped_column(Text, default="")
    edited_text: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[list] = mapped_column(JSONType, default=list)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    grounded: Mapped[bool] = mapped_column(Boolean, default=True)
    critic_notes: Mapped[str] = mapped_column(Text, default="")
    revisions: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped["TailoringSession"] = relationship(back_populates="suggestions")

    __table_args__ = (
        Index("ix_suggestions_session_status", "session_id", "status"),
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    resume_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resumes.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    suggestion_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    resume: Mapped["Resume"] = relationship(back_populates="messages")

    __table_args__ = (
        Index("ix_chat_messages_resume_created", "resume_id", "created_at"),
    )


class VectorDoc(Base):
    __tablename__ = "vector_docs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    # NULL for global corpora (skill_taxonomy, ats_rules).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    corpus: Mapped[str] = mapped_column(String(40), index=True)
    # Not a FK: global rows have no resume.
    resume_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    target_ref: Mapped[str] = mapped_column(String(80), default="")
    placement: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text)
    doc_metadata: Mapped[dict] = mapped_column(JSONType, default=dict)
    embedding: Mapped[list] = mapped_column(EmbeddingType)

    __table_args__ = (
        CheckConstraint(
            "(corpus IN ('skill_taxonomy','ats_rules') AND user_id IS NULL)"
            " OR (corpus NOT IN ('skill_taxonomy','ats_rules') AND user_id IS NOT NULL)",
            name="ck_vector_docs_scope",
        ),
        Index("ix_vector_docs_scope", "user_id", "corpus"),
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    thread_id: Mapped[str] = mapped_column(String(80), default="")
    agent: Mapped[str] = mapped_column(String(40))
    task: Mapped[str] = mapped_column(String(80), default="")
    model: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(20), default="ok")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (Index("ix_agent_runs_user_thread", "user_id", "thread_id"),)
