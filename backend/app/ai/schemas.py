"""Structured-output schemas for the agents.

These constrain the provider's json_schema mode. Measured behaviour on the free
tier is that providers *accept* json_schema and then reply with markdown prose
anyway, so schemas used on that path may also offer `from_prose()` as a last
resort. See app/ai/prose.py for why that is a salvage path and not a parser.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.ai import prose


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


class RevisionDraft(BaseModel):
    """One targeted rewrite. Single-item sibling of DraftSuggestion."""

    suggested_text: str = Field(description="The improved text")
    keywords: list[str] = Field(
        default_factory=list, description="Terms added or surfaced, for highlighting"
    )
    reasoning: str = Field(description="Why this is better, one or two sentences")


class WriterOutput(BaseModel):
    suggestions: list[DraftSuggestion] = Field(default_factory=list)


class CriticVerdict(BaseModel):
    approved: bool
    notes: str = Field(default="", description="Why it was rejected, if it was")
    severity: str = Field(default="none", description="none, minor or fabrication")

    @classmethod
    def from_prose(cls, text: str) -> CriticVerdict | None:
        """Read a verdict out of a markdown reply, or None if it is unclear.

        Fails closed on purpose: anything this cannot read unambiguously
        returns None, and the caller degrades to the rule engine exactly as it
        does today. Guessing would be worse than the status quo, because the
        dangerous direction -- reading a rejection as an approval -- ships a
        fabrication to someone who will be asked about it in an interview.
        """
        approved = prose.read_verdict(text)
        if approved is None:
            return None
        if approved:
            return cls(approved=True, notes="", severity="none")
        return cls(
            approved=False,
            notes=prose.first_sentences(text),
            severity="fabrication" if prose.looks_like_fabrication(text) else "minor",
        )


class CriticVerdictItem(CriticVerdict):
    """One verdict inside a batched critic reply."""

    index: int = Field(description="1-based index of the draft being judged")


class CriticBatch(BaseModel):
    """All verdicts for one batched critic call.

    Batching exists because the free tier allows one request per minute per
    model per account: a six-draft tailor run made as six calls is guaranteed
    to be rate-limited partway through.
    """

    verdicts: list[CriticVerdictItem] = Field(default_factory=list)

    @classmethod
    def from_prose(cls, text: str) -> CriticBatch | None:
        chunks = prose.split_numbered(text)
        if not chunks:
            # No numbering: only salvageable if it is a single verdict, which
            # is the shape the one-draft path produces.
            single = CriticVerdict.from_prose(text)
            if single is None:
                return None
            return cls(verdicts=[CriticVerdictItem(index=1, **single.model_dump())])

        verdicts: list[CriticVerdictItem] = []
        for index, chunk in chunks:
            verdict = CriticVerdict.from_prose(chunk)
            if verdict is None:
                return None  # partial reads are not usable; all or nothing
            verdicts.append(CriticVerdictItem(index=index, **verdict.model_dump()))
        return cls(verdicts=verdicts)


class ChatReply(BaseModel):
    message: str = Field(description="Reply shown to the user")
    proposes_change: bool = Field(default=False)
    target_ref: str = Field(default="")
    suggested_text: str = Field(default="")
    reasoning: str = Field(default="")
