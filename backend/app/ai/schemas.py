"""Structured-output schemas for the agents.

These constrain the provider's json_schema mode. Measured behaviour on the free
tier is that providers *accept* json_schema and then reply with markdown prose
anyway, so schemas used on that path may also offer `from_prose()` as a last
resort. See app/ai/prose.py for why that is a salvage path and not a parser.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.ai import prose


class ExtractedKeyword(BaseModel):
    term: str = Field(description="Exact spelling used in the job description")
    importance: str = Field(description="required, preferred or nice_to_have")
    category: str = Field(description="language, platform, tool, practice or domain")


class JDAnalysis(BaseModel):
    role_title: str = Field(default="", description="Normalised role title")
    seniority: str = Field(default="", description="junior, mid, senior or staff")
    keywords: list[ExtractedKeyword] = Field(default_factory=list)


class CategoryNotes(BaseModel):
    """The four scoring categories as EXPLICIT fields, not a free-form dict.

    `dict[str, str]` renders as `{"type": "object", "additionalProperties":
    {"type": "string"}}`, and Groq's strict json_schema mode rejects that
    outright:

        invalid JSON schema for response_format: 'ScoringCommentary':
        /properties/category_notes: `additionalProperties:false` must be
        set on every object

    An open-ended map cannot satisfy that rule by construction -- its whole
    point is that the keys are not known ahead of time. Here they always
    were: the rule engine scores exactly these four categories and nothing
    else, so the dict was never buying flexibility, only an unprovable
    schema. Naming the fields costs nothing and makes the contract real.

    This failed on EVERY model in the chain, so there was no fallback: the
    scoring agent silently degraded to rule-engine commentary on every
    single analysis, and the only trace was a line in the server log.
    """

    # 2026-10-25: the scoring table grew — skills and projects are scored
    # categories now, so the contract names them too. Keeping contact/format
    # fields lets the model acknowledge advisory-only findings; the strengths
    # filter zeroes them either way.
    model_config = ConfigDict(extra="forbid")

    contact: str = ""
    summary: str = ""
    skills: str = ""
    experience: str = ""
    projects: str = ""
    format: str = ""


class CategoryStrengths(BaseModel):
    """What the resume already does RIGHT, per category.

    Explicit fields for the same reason as CategoryNotes above: a
    `dict[str, list[str]]` renders `additionalProperties: {...}`, which
    Groq's strict json_schema mode rejects on every model at once. The
    categories are fixed by the rule engine, so naming them costs nothing.

    This is the only part of the analysis that is asked to say something
    POSITIVE, which makes it the only part with an incentive to flatter --
    the rule engine emits findings, and a finding is by definition a
    problem. An always-three-items list would be the vacuity failure from
    writer_report wearing a different hat, so the prompt demands evidence
    and `agents._clean_strengths` enforces what a prompt cannot.
    """

    model_config = ConfigDict(extra="forbid")

    contact: list[str] = Field(default_factory=list)
    summary: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    experience: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    format: list[str] = Field(default_factory=list)


class ScoringCommentary(BaseModel):
    """Prose only. The numbers come from the rule engine."""

    model_config = ConfigDict(extra="forbid")

    headline: str = Field(description="One-sentence verdict")
    category_notes: CategoryNotes = Field(
        default_factory=CategoryNotes,
        description=("Per-category advice for summary, skills, experience, "
                     "projects; contact and format are advisory checks"),
    )
    # Rides on the SAME call as the notes above, so "What's Working Well"
    # costs no extra tokens and cannot disagree with the commentary beside
    # it -- two calls could, and at 40 analyses/month that matters.
    strengths: CategoryStrengths = Field(
        default_factory=CategoryStrengths,
        description=(
            "Per category, up to 3 things the resume already does well. "
            "Quote or name the specific text that proves each one. "
            "Return an empty list for a category with nothing genuinely good "
            "-- an empty list is a correct answer, padding is not."
        ),
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

    @classmethod
    def from_prose(cls, text: str) -> RevisionDraft | None:
        """Recover a rewrite from a model that answered in plain text.

        Added after `nvidia:meta/muse-glimmer-30b` failed EVERY case of the
        writer corpus with

            unparsable: Invalid JSON: expected value at line 1 column 1
            input_value='Reduced CI pipeline runtime from 22 minutes to 9 ...'

        -- which is a correct, well-formed rewrite that was thrown away for
        not being wrapped in JSON. With no salvage rung the whole chain fell
        through to the critic's own model, collapsing writer/critic
        independence and making the run inconclusive.

        This is the single most common LLM failure mode in this project, and
        every other schema that meets it (CriticVerdict, CriticBatch,
        AtsReview) already has this. RevisionDraft was the gap.

        Fails closed, like the others: anything that does not clearly look
        like replacement prose returns None and the caller degrades exactly
        as it does today. The danger here is specific -- `suggested_text` is
        written into somebody's resume -- so a refusal, a question, a
        multi-paragraph explanation or leftover JSON is refused rather than
        guessed at.

        Safety is unchanged downstream: a salvaged draft still passes through
        `_hard_check` (verbatim-original, introduced-findings, and the
        digits-only fabrication guard) and still goes to the critic. This
        recovers the TEXT, never the approval.
        """
        if not prose.looks_like_a_rewrite(text):
            return None
        cleaned = prose.first_sentences(text, limit=900).strip()
        if not cleaned:
            return None
        return cls(
            suggested_text=cleaned,
            keywords=[],
            # Empty rather than invented. The UI shows this to the user, and
            # a made-up justification for a real edit is its own small lie.
            reasoning="",
        )


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



class SkillExpansion(BaseModel):
    acronym: str = ""
    expanded: str = ""


class MetricAdvisory(BaseModel):
    """A judgement call about one number on the resume. Never a score.

    The model's job is plausibility -- "10 jobs/hour sounds low for a
    microservices pipeline". This is deliberately NOT a Finding: a Finding
    carries points, and a second opinion that wanders run to run cannot be
    allowed to move a number that must be reproducible.
    """

    bullet: str = Field(
        description="Exactly 'exp_N.bullet_M' as given in the input list"
    )
    quote: str = Field(
        description="The metric text verbatim -- the number and its unit, "
        "short enough to sit inside one clause"
    )
    advice: str = Field(description="One or two sentences of advice")


class AtsReview(BaseModel):
    """Judgement calls about machine-readability. Never a score.

    Carries `from_prose()` deliberately. The salvage ladder's third rung only
    exists for schemas that opt in, and the writer path has no reader -- so a
    model that answers correctly but in markdown is thrown away there. This
    path is advisory and low-stakes, which makes a lenient reader clearly
    worth having: the worst case for a misread is one unhelpful tip.
    """

    expansions: list[SkillExpansion] = Field(default_factory=list)
    plausibility: list[MetricAdvisory] = Field(default_factory=list)
    note: str = ""

    @classmethod
    def from_prose(cls, text: str) -> AtsReview | None:
        """Read `Experience` / `PMP = Project Management Professional` lines."""
        flat = (text or "").strip()
        if not flat:
            return None
        expansions: list[SkillExpansion] = []
        for line in flat.splitlines():
            line = line.strip().lstrip("-*0123456789. ").strip()
            for sep in ("=", "->", "—", ":"):
                if sep in line:
                    left, right = line.split(sep, 1)
                    left, right = left.strip(), right.strip()
                    if left.isupper() and 1 < len(left) <= 6 and 2 < len(right) < 80:
                        expansions.append(
                            SkillExpansion(acronym=left, expanded=right)
                        )
                    break
        if not expansions:
            return None
        return cls(expansions=expansions, note="")


class ChatReply(BaseModel):
    message: str = Field(description="Reply shown to the user")
    proposes_change: bool = Field(default=False)
    target_ref: str = Field(default="")
    suggested_text: str = Field(default="")
    reasoning: str = Field(default="")
