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
    #: Advisories about the UPLOADED FILE's machine-readability.
    #:
    #: Distinct from `parse_note`, which reports that reading FAILED. These
    #: accompany a successful parse and say the reading may have been shaky
    #: -- two columns, a scan, a heading we did not recognise. Advisory
    #: only: none of them is a scored finding.
    parse_advisories: list[str] = []


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
    # Recomputed from the CURRENT structured_data on every read, not read
    # back from the stored row -- see AnalysisService.latest(). `created_at`
    # therefore dates the prose below, not these numbers.
    overall_score: int
    category_scores: dict = Field(default_factory=dict)
    role_tags: list = Field(default_factory=list)
    trace: dict = Field(default_factory=dict, exclude=True)
    created_at: datetime
    # True when the resume changed after the prose was written. Mirrors
    # AnalysisStepsOut.commentary_stale so both screens stamp it the same way.
    commentary_stale: bool = False

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
    #: Advisory (0-point) gate steps — the contact knockout filter and the
    #: pass/fail format parser blocker, 2026-10-25 ruling. Findings yes,
    #: points no.
    advisory: bool = False
    findings: list[FindingOut] = Field(default_factory=list)


class AnalysisStepsOut(BaseModel):
    resume_id: uuid.UUID
    overall_score: int
    max_score: int
    points_available: int
    steps: list[StepOut] = Field(default_factory=list)

    # The ScoringAgent's prose, carried alongside the live steps. Already
    # generated and persisted on every analysis; until now no screen read it.
    commentary: CommentaryOut = Field(default_factory=CommentaryOut)
    # True when the resume has changed since that prose was written. The
    # steps beside it are recomputed live, so the two CAN disagree, and the
    # UI must be able to say so rather than presenting stale prose as fact.
    commentary_stale: bool = False


# ---------- tailoring ----------
class TailorIn(BaseModel):
    jd_title: str = ""
    jd_content: str
    # Tailoring edits the resume IN PLACE so the Analysis and Tailor tabs
    # always describe the same document.
    #
    # This used to default to True. Forking produced a child row, the
    # session pointed at the child, and the two tabs silently edited
    # DIFFERENT resumes: accept a suggestion in Tailor and Analysis would
    # go on showing the untouched original. Opt in per request to keep a
    # pristine base alongside per-job variants.
    fork: bool = False


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

    # --- clarifying-question flow ---
    # `question` is non-empty only on a needs_input card. `attested` stays
    # True after answering, which is how the UI keeps showing "you told us
    # this" on a card whose status has moved on to pending.
    question: str = ""
    answer: str = ""
    attested: bool = False
    requires_text: bool = False

    # What accepting this would do to the RESUME score -- the same currency
    # the Analysis tab prints on its cards, so the two tabs can be compared.
    #
    # Deterministic and free: apply the patch to a copy, rescore with the
    # same rule engine the UI shows, subtract. `introduced_findings` was
    # already computing the patched score during review and throwing the
    # number away.
    #
    # Usually 0 -- a keyword swap that neither helps nor hurts the rule
    # engine is the common case, and saying "no score change" is more
    # honest than hiding the field. It should never be negative: the
    # review gate rejects any draft that introduces a finding, and
    # `Delta score == points` holds, so a negative here means that gate has
    # a hole. Surfaced rather than clamped, so the hole would be visible.
    score_delta: int = 0

    # True when the resume text this was written against has since changed.
    #
    # `act()` already blocks applying one of these -- it would overwrite newer
    # work -- but it blocks at click time, after the user has read the card
    # and decided. Surfacing it on the card turns a 409 into a state the UI
    # can show. Only ever true for pending suggestions.
    is_stale: bool = False


class SuggestionBuckets(BaseModel):
    active: list[SuggestionOut]
    matched: list[SuggestionOut]
    rejected: list[SuggestionOut]


class RewriteRequest(BaseModel):
    """Body for POST /resumes/{id}/sections/{ref}/rewrite. All optional."""

    finding_id: str = ""
    # Supersede the existing pending draft for this ref instead of returning it.
    regenerate: bool = False


class SuggestionAnswer(BaseModel):
    """Answering a clarifying question.

    Two fields because the reference offers two affordances and they attest
    different amounts. `answer` is the user's own sentence. `confirmed` is
    the bare Yes button, which is accepted ONLY on a question with no
    duration attached -- see Suggestion.requires_text.
    """

    answer: str = ""
    confirmed: bool = False


class SuggestionAction(BaseModel):
    action: Literal["accept", "reject", "edit"]
    edited_text: str | None = None


# ---------- chat ----------
class ChatMessageOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    resume_id: uuid.UUID
    #: "user" | "assistant" | "event" -- see models.ChatMessage.role.
    role: str
    content: str
    suggestion_id: uuid.UUID | None = None
    #: Structured payload for `event` rows; empty for conversation turns.
    meta: dict = Field(default_factory=dict)
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


class AcceptAllIn(BaseModel):
    """Scope for a document-level Accept All.

    Omitting `session_id` means every pending suggestion on the resume,
    which is what the Analysis pane wants; the Tailor pane passes its
    session so one job's run cannot sweep up another's.
    """

    session_id: uuid.UUID | None = None


class SkippedSuggestion(BaseModel):
    id: str
    reason: str


class AcceptAllOut(BaseModel):
    applied: list[str]
    applied_count: int
    skipped: list[SkippedSuggestion]
    skipped_count: int
    #: The cursor BEFORE the batch -- the seq Decline All restores to.
    undo_to_version: int
    version_cursor: int


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
