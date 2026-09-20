"""Structured-output schemas for the agents.

These constrain the provider's json_schema mode, so a malformed reply raises
instead of silently degrading into prose.
"""

from pydantic import BaseModel, Field


class ExtractedKeyword(BaseModel):
    term: str = Field(description="Exact spelling used in the job description")
    importance: str = Field(description="required, preferred or nice_to_have")
    category: str = Field(description="language, platform, tool, practice or domain")


class JDAnalysis(BaseModel):
    role_title: str = Field(default="", description="Normalised role title")
    seniority: str = Field(default="", description="junior, mid, senior or staff")
    keywords: list[ExtractedKeyword] = Field(default_factory=list)


class ScoringCommentary(BaseModel):
    """Prose only. The numbers come from the rule engine."""

    headline: str = Field(description="One-sentence verdict")
    category_notes: dict[str, str] = Field(
        default_factory=dict,
        description="Per-category advice keyed by contact, summary, experience, format",
    )


class DraftSuggestion(BaseModel):
    target_ref: str = Field(description="Exact target_ref of the bullet being rewritten")
    original_text: str = Field(description="The bullet text verbatim, unmodified")
    suggested_text: str = Field(description="The rewrite, under 30 words")
    keywords: list[str] = Field(default_factory=list)
    reasoning: str = Field(description="Why this rewrite helps, one sentence")


class WriterOutput(BaseModel):
    suggestions: list[DraftSuggestion] = Field(default_factory=list)


class CriticVerdict(BaseModel):
    approved: bool
    notes: str = Field(default="", description="Why it was rejected, if it was")
    severity: str = Field(default="none", description="none, minor or fabrication")


class ChatReply(BaseModel):
    message: str = Field(description="Reply shown to the user")
    proposes_change: bool = Field(default=False)
    target_ref: str = Field(default="")
    suggested_text: str = Field(default="")
    reasoning: str = Field(default="")
