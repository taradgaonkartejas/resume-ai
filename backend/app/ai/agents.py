"""The six specialist agents (DESIGN.md §6.3).

Each agent has the same shape: gather scoped context, call the gateway, and
fall back to the deterministic path when the LLM is unavailable or returns
something unusable. No agent talks to HTTP or commits a transaction.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from app.ai import llm, prompts
from app.ai.schemas import (
    ChatReply,
    CriticVerdict,
    DraftSuggestion,
    JDAnalysis,
    ScoringCommentary,
    WriterOutput,
)
from app.ai.vectorstore import VectorStore
from app.services import resume_ops
from app.services.heuristics import extract_keywords

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 6


@dataclass
class AgentOutcome:
    """Result plus telemetry, so graphs can record agent_runs rows."""

    value: object
    agent: str
    model: str = "rule-engine"
    latency_ms: int = 0
    status: str = "ok"
    tokens_in: int = 0
    tokens_out: int = 0
    used_llm: bool = False
    notes: list[str] = field(default_factory=list)


def _context_block(hits) -> str:
    if not hits:
        return "(none)"
    return "\n".join(f"- {h.content}" for h in hits[:5])


# ---------------------------------------------------------------- JD analyst
def analyse_jd(
    jd_text: str, store: VectorStore | None = None
) -> AgentOutcome:
    """Extract required keywords. Taxonomy RAG sharpens canonical spellings."""
    context = "(none)"
    if store is not None:
        try:
            hits = store.search(jd_text[:800], corpus="skill_taxonomy", k=5)
            context = _context_block(hits)
        except Exception as exc:  # noqa: BLE001
            logger.warning("taxonomy retrieval failed: %s", exc)

    if llm.is_configured():
        try:
            parsed, call = llm.structured(
                task="jd_analysis",
                system=prompts.JD_ANALYST.format(context=context),
                user=jd_text[:6000],
                schema=JDAnalysis,
            )
            terms = [k.term.lower() for k in parsed.keywords if k.term.strip()]
            if terms:
                return AgentOutcome(
                    value={
                        "keywords": terms,
                        "role_title": parsed.role_title,
                        "seniority": parsed.seniority,
                    },
                    agent="jd_analyst",
                    model=call.model,
                    latency_ms=call.latency_ms,
                    tokens_in=call.tokens_in,
                    tokens_out=call.tokens_out,
                    used_llm=True,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("JD analyst fell back to rules: %s", exc)

    return AgentOutcome(
        value={"keywords": extract_keywords(jd_text), "role_title": "", "seniority": ""},
        agent="jd_analyst",
        notes=["rule-engine keyword extraction"],
    )


# ------------------------------------------------------------- scoring agent
def score_commentary(
    structured_data: dict, rule_result: dict, store: VectorStore | None = None
) -> AgentOutcome:
    """Prose commentary only. The number is never the LLM's to decide."""
    context = "(none)"
    if store is not None:
        try:
            weak = [
                name
                for name, cat in rule_result["category_scores"].items()
                if cat["score"] < cat["max"] * 0.7
            ]
            probe = " ".join(weak) or "resume quality"
            hits = store.search(probe, corpus="ats_rules", k=5)
            context = _context_block(hits)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ATS retrieval failed: %s", exc)

    fallback_notes = {
        name: " ".join(cat["notes"]) or "Looks good."
        for name, cat in rule_result["category_scores"].items()
    }

    if llm.is_configured():
        try:
            summary = resume_ops.to_plain_text(structured_data)[:4000]
            payload = (
                f"Score: {rule_result['overall_score']}/100\n"
                f"Breakdown: {rule_result['category_scores']}\n\n"
                f"Resume:\n{summary}"
            )
            parsed, call = llm.structured(
                task="scoring",
                system=prompts.SCORING.format(context=context),
                user=payload,
                schema=ScoringCommentary,
                temperature=0.3,
            )
            return AgentOutcome(
                value={
                    "headline": parsed.headline,
                    "category_notes": parsed.category_notes or fallback_notes,
                },
                agent="scoring",
                model=call.model,
                latency_ms=call.latency_ms,
                tokens_in=call.tokens_in,
                tokens_out=call.tokens_out,
                used_llm=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scoring agent fell back to rules: %s", exc)

    score = rule_result["overall_score"]
    verdict = (
        "Strong resume with minor gaps."
        if score >= 80
        else "Solid foundation, several fixable weaknesses."
        if score >= 60
        else "Needs substantial work before applying."
    )
    return AgentOutcome(
        value={"headline": verdict, "category_notes": fallback_notes},
        agent="scoring",
        notes=["rule-engine commentary"],
    )


# -------------------------------------------------------------- writer agent
def write_suggestions(
    structured_data: dict,
    gap_keywords: list[str],
    user_id: uuid.UUID | None = None,
    resume_id: uuid.UUID | None = None,
    store: VectorStore | None = None,
    limit: int = MAX_SUGGESTIONS,
) -> AgentOutcome:
    """Draft rewrites. Retrieval is scoped to this user's own bullets."""
    bullets = list(resume_ops.iter_bullets(structured_data))
    if not bullets or not gap_keywords:
        return AgentOutcome(value=[], agent="writer", notes=["nothing to do"])

    context = "(none)"
    if store is not None and user_id is not None:
        try:
            hits = store.search(
                " ".join(gap_keywords[:8]),
                corpus="resume_bullets",
                user_id=user_id,
                resume_id=resume_id,
                k=5,
            )
            context = _context_block(hits)
        except Exception as exc:  # noqa: BLE001
            logger.warning("resume retrieval failed: %s", exc)

    if llm.is_configured():
        try:
            catalogue = "\n".join(
                f"{ref} | {placement} | {text}" for ref, placement, text in bullets[:20]
            )
            payload = (
                f"Missing job-description keywords: {', '.join(gap_keywords[:15])}\n\n"
                f"Bullets (target_ref | placement | text):\n{catalogue}\n\n"
                f"Rewrite at most {limit}. Use the exact target_ref and copy "
                f"original_text verbatim."
            )
            parsed, call = llm.structured(
                task="writing",
                system=prompts.WRITER.format(context=context),
                user=payload,
                schema=WriterOutput,
                temperature=0.4,
            )
            drafts = _normalise_drafts(parsed.suggestions, structured_data, limit)
            if drafts:
                return AgentOutcome(
                    value=drafts,
                    agent="writer",
                    model=call.model,
                    latency_ms=call.latency_ms,
                    tokens_in=call.tokens_in,
                    tokens_out=call.tokens_out,
                    used_llm=True,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Writer fell back to rules: %s", exc)

    return AgentOutcome(
        value=_rule_drafts(bullets, gap_keywords, limit),
        agent="writer",
        notes=["rule-engine drafts"],
    )


def _normalise_drafts(
    drafts: list[DraftSuggestion], data: dict, limit: int
) -> list[dict]:
    """Repair what is repairable; drop what is not. The critic checks again."""
    out: list[dict] = []
    for draft in drafts[: limit * 2]:
        ref = (draft.target_ref or "").strip()
        if not resume_ops.exists(data, ref):
            continue
        actual = resume_ops.resolve(data, ref)
        # The model sometimes paraphrases original_text; trust the resume.
        section = (
            "summary"
            if ref.startswith("summary")
            else "projects"
            if ref.startswith("prj")
            else "skills"
            if ref.startswith("skills")
            else "experience"
        )
        placement = next(
            (p for r, p, _ in resume_ops.iter_bullets(data) if r == ref), ""
        )
        text = (draft.suggested_text or "").strip()
        if not text or text == actual:
            continue
        out.append(
            {
                "section": section,
                "target_ref": ref,
                "placement": placement,
                "original_text": actual,
                "suggested_text": text,
                "keywords": [k.lower() for k in (draft.keywords or [])],
                "reasoning": draft.reasoning or "",
            }
        )
        if len(out) >= limit:
            break
    return out


def _rule_drafts(bullets, gaps: list[str], limit: int) -> list[dict]:
    drafts: list[dict] = []
    for target_ref, placement, text in bullets:
        if len(drafts) >= limit:
            break
        if not text.strip():
            continue
        chunk = gaps[len(drafts) : len(drafts) + 2]
        if not chunk:
            break
        joined = " and ".join(chunk)
        section = (
            "summary"
            if target_ref.startswith("summary")
            else "projects"
            if target_ref.startswith("prj")
            else "experience"
        )
        drafts.append(
            {
                "section": section,
                "target_ref": target_ref,
                "placement": placement,
                "original_text": text,
                "suggested_text": text.rstrip(".") + f", leveraging {joined}.",
                "keywords": chunk,
                "reasoning": f"Adds missing job-description keyword(s): {joined}.",
            }
        )
    return drafts


# -------------------------------------------------------------- critic agent
def review_draft(draft: dict, structured_data: dict) -> AgentOutcome:
    """Hard checks first, then optional LLM judgement.

    The hard checks are not negotiable and run with or without a key:
    the target_ref must resolve, and original_text must match verbatim.
    """
    ref = draft.get("target_ref", "")
    if not resume_ops.exists(structured_data, ref):
        return AgentOutcome(
            value=CriticVerdict(
                approved=False, notes=f"target_ref {ref} does not resolve",
                severity="fabrication",
            ),
            agent="critic",
            notes=["hard check: unresolvable target_ref"],
        )

    actual = resume_ops.resolve(structured_data, ref)
    if actual != draft.get("original_text", ""):
        return AgentOutcome(
            value=CriticVerdict(
                approved=False,
                notes="original_text does not match the resume verbatim",
                severity="fabrication",
            ),
            agent="critic",
            notes=["hard check: original_text mismatch"],
        )

    if llm.is_configured():
        try:
            payload = (
                f"Original bullet:\n{actual}\n\n"
                f"Proposed rewrite:\n{draft.get('suggested_text','')}\n\n"
                f"Keywords the writer claims to have added: "
                f"{', '.join(draft.get('keywords', []))}"
            )
            parsed, call = llm.structured(
                task="critique",
                system=prompts.CRITIC,
                user=payload,
                schema=CriticVerdict,
                temperature=0.0,
            )
            return AgentOutcome(
                value=parsed,
                agent="critic",
                model=call.model,
                latency_ms=call.latency_ms,
                tokens_in=call.tokens_in,
                tokens_out=call.tokens_out,
                used_llm=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Critic fell back to rules: %s", exc)

    return AgentOutcome(
        value=_rule_critique(actual, draft),
        agent="critic",
        notes=["rule-engine critique"],
    )


_FABRICATION_HINTS = ("led a team of", "promoted to", "managed a budget")


def _rule_critique(original: str, draft: dict) -> CriticVerdict:
    suggested = draft.get("suggested_text", "")
    if not suggested.strip():
        return CriticVerdict(approved=False, notes="empty rewrite", severity="minor")

    # A new number that was not in the original is a fabricated metric.
    import re

    originals = set(re.findall(r"\d+(?:\.\d+)?", original))
    proposed = set(re.findall(r"\d+(?:\.\d+)?", suggested))
    invented = proposed - originals
    if invented:
        return CriticVerdict(
            approved=False,
            notes=f"introduces unverifiable metric(s): {', '.join(sorted(invented))}",
            severity="fabrication",
        )

    lowered = suggested.lower()
    for hint in _FABRICATION_HINTS:
        if hint in lowered and hint not in original.lower():
            return CriticVerdict(
                approved=False,
                notes=f"introduces unsupported claim: {hint}",
                severity="fabrication",
            )

    if len(suggested.split()) > 45:
        return CriticVerdict(
            approved=False, notes="rewrite is too long", severity="minor"
        )

    return CriticVerdict(approved=True, notes="", severity="none")


# ---------------------------------------------------------------- chat agent
def chat_reply(
    prompt: str,
    structured_data: dict,
    user_id: uuid.UUID | None = None,
    resume_id: uuid.UUID | None = None,
    store: VectorStore | None = None,
) -> AgentOutcome:
    context = "(none)"
    if store is not None and user_id is not None:
        try:
            hits = store.search(
                prompt,
                corpus="resume_bullets",
                user_id=user_id,
                resume_id=resume_id,
                k=4,
            )
            context = _context_block(hits)
        except Exception as exc:  # noqa: BLE001
            logger.warning("chat retrieval failed: %s", exc)

    if llm.is_configured():
        try:
            refs = "\n".join(
                f"{ref} | {text}" for ref, _, text in resume_ops.iter_bullets(structured_data)
            )
            payload = (
                f"User: {prompt}\n\n"
                f"Addressable bullets (target_ref | text):\n{refs[:3000]}"
            )
            parsed, call = llm.structured(
                task="chat",
                system=prompts.CHAT.format(context=context),
                user=payload,
                schema=ChatReply,
                temperature=0.5,
            )
            if parsed.proposes_change and not resume_ops.exists(
                structured_data, parsed.target_ref
            ):
                parsed.proposes_change = False  # ungrounded: reply only
            return AgentOutcome(
                value=parsed,
                agent="chat",
                model=call.model,
                latency_ms=call.latency_ms,
                tokens_in=call.tokens_in,
                tokens_out=call.tokens_out,
                used_llm=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Chat agent fell back to rules: %s", exc)

    return AgentOutcome(value=None, agent="chat", notes=["rule-engine reply"])
