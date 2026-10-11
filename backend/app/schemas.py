import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

ORM = ConfigDict(from_attributes=True)


# ---------- users / templates ----------
class UserOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    email: str
    name: str
    title: str
    avatar_color: str
    chat_tokens_left: int


class TemplateOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    key: str
    name: str
    description: str
    design_tokens: dict = Field(default_factory=dict)


# ---------- resumes ----------
class ResumeOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    structured_data: dict = Field(default_factory=dict)
    storage_key: str
    parse_status: str
    parse_note: str
    template_key: str
    kind: str = "base"
    parent_id: uuid.UUID | None = None
    tailored_for: str = ""
    version_cursor: int
    updated_at: datetime


class ResumeSummaryOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    title: str
    template_key: str
    parse_status: str
    kind: str = "base"
    parent_id: uuid.UUID | None = None
    tailored_for: str = ""
    version_cursor: int
    updated_at: datetime
    # Latest analysis score, or None when never analysed. None is NOT 0:
    # a card showing 0% would read as a terrible resume rather than an
    # unmeasured one.
    overall_score: int | None = None
    # Enough content to render the card thumbnail without a fetch per card.
    structured_data: dict = Field(default_factory=dict)


class ResumeForkIn(BaseModel):
    title: str = ""
    tailored_for: str = ""


class ResumeRenameIn(BaseModel):
    title: str


class ResumeCreateIn(BaseModel):
    title: str = "Untitled resume"


class ResumeDataIn(BaseModel):
    structured_data: dict


class TemplateIn(BaseModel):
    template_key: str


class ParseStatusOut(BaseModel):
    resume_id: str
    parse_status: str
    parse_note: str


# ---------- analysis ----------
class AtsExpansionOut(BaseModel):
    acronym: str = ""
    expanded: str = ""


class AtsReviewOut(BaseModel):
    """Advisory machine-readability notes. Never affects the score."""

    expansions: list[AtsExpansionOut] = Field(default_factory=list)
    note: str = ""


class CommentaryOut(BaseModel):
    headline: str = ""
    category_notes: dict = Field(default_factory=dict)


class AnalysisOut(BaseModel):
    """The analysis result as the client sees it.

    `trace` is carried but excluded: it holds node names, model ids and
    latencies, which are operator data rather than user data. What the user
    is owed is the prose those nodes produced -- the ScoringAgent headline
    and the ATS review -- so those are lifted out as first-class fields.

    They were previously computed, persisted and then never returned by any
    endpoint, which made the whole LLM commentary layer write-only: it cost a
    model call per analysis and nobody could read the result.
    """

    model_config = ORM
    id: uuid.UUID
    resume_id: uuid.UUID
    overall_score: int
    category_scores: dict = Field(default_factory=dict)
    role_tags: list = Field(default_factory=list)
    trace: dict = Field(default_factory=dict, exclude=True)
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def commentary(self) -> CommentaryOut:
        raw = self.trace.get("commentary") or {}
        return CommentaryOut.model_validate(raw if isinstance(raw, dict) else {})

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ats_review(self) -> AtsReviewOut:
        raw = self.trace.get("ats_review") or {}
        return AtsReviewOut.model_validate(raw if isinstance(raw, dict) else {})


# ---------- guided step editor ----------
class FindingOut(BaseModel):
    """One actionable recommendation.

    `points` is a promise: fixing this raises overall_score by exactly that
    much (tests/test_findings.py enforces it).
    """

    id: str
    category: str
    target_ref: str
    severity: str
    points: int
    message: str
    fix_hint: str
    action: str = ""
    meta: dict = Field(default_factory=dict)


class StepOut(BaseModel):
    id: str
    index: int
    title: str
    description: str
    score: int
    max: int
    points_available: int
    finding_count: int
    status: str
    findings: list[FindingOut] = Field(default_factory=list)


class AnalysisStepsOut(BaseModel):
    resume_id: uuid.UUID
    overall_score: int
    max_score: int
    points_available: int
    steps: list[StepOut] = Field(default_factory=list)


# ---------- tailoring ----------
class TailorIn(BaseModel):
    jd_title: str = ""
    jd_content: str
    # Tailoring forks a new resume so the base stays pristine and each job
    # gets its own card. false keeps the old in-place behaviour.
    fork: bool = True


class TailorSessionOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    resume_id: uuid.UUID
    job_description_id: uuid.UUID
    match_percent: float
    baseline_percent: float
    matched_keywords: list = Field(default_factory=list)
    gap_keywords: list = Field(default_factory=list)
    thread_id: str
    graph_state: str
    created_at: datetime


class SuggestionOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    session_id: uuid.UUID | None = None
    resume_id: uuid.UUID
    origin: str
    section: str
    target_ref: str
    placement: str
    original_text: str
    suggested_text: str
    edited_text: str
    keywords: list = Field(default_factory=list)
    reasoning: str
    status: str
    grounded: bool
    critic_notes: str
    revisions: int


class SuggestionBuckets(BaseModel):
    active: list[SuggestionOut]
    matched: list[SuggestionOut]
    rejected: list[SuggestionOut]


class RewriteRequest(BaseModel):
    """Body for POST /resumes/{id}/sections/{ref}/rewrite. All optional."""

    finding_id: str = ""
    # Supersede the existing pending draft for this ref instead of returning it.
    regenerate: bool = False


class SuggestionAction(BaseModel):
    action: Literal["accept", "reject", "edit"]
    edited_text: str | None = None


# ---------- chat ----------
class ChatMessageOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    resume_id: uuid.UUID
    role: str
    content: str
    suggestion_id: uuid.UUID | None = None
    created_at: datetime


class ChatHistoryOut(BaseModel):
    messages: list[ChatMessageOut]
    quick_actions: list[str]
    tokens_left: int


class ChatIn(BaseModel):
    content: str


class ChatReplyOut(BaseModel):
    message: ChatMessageOut
    suggestion: SuggestionOut | None = None
    tokens_left: int


# ---------- versions ----------
class VersionOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    resume_id: uuid.UUID
    seq: int
    change_source: str
    label: str
    created_at: datetime


# ---------- misc ----------
class JobDescriptionOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    title: str
    content: str
    extracted_keywords: list = Field(default_factory=list)


class AgentRunOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    thread_id: str
    agent: str
    task: str
    model: str
    status: str
    latency_ms: int
    tokens_in: int
    tokens_out: int
    created_at: datetime


class HealthOut(BaseModel):
    status: str
    database: str
    pgvector: bool
    storage: str
    llm_configured: bool
    model: str
    users: int


class DeleteOut(BaseModel):
    deleted: str
    vectors_removed: int
    objects_removed: int
    # Forks are detached, not cascaded: deleting a base must never silently
    # delete the tailored versions already sent to employers.
    children_orphaned: int = 0


class MessageOut(BaseModel):
    detail: str
    data: Any | None = None
