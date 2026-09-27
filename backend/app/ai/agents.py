"""The six specialist agents (DESIGN.md §6.3).

Each agent has the same shape: gather scoped context, call the gateway, and
fall back to the deterministic path when the LLM is unavailable or returns
something unusable. No agent talks to HTTP or commits a transaction.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field

from app.ai import llm, prompts
from app.ai.schemas import (
    ChatReply,
    CriticBatch,
    CriticVerdict,
    DraftSuggestion,
    JDAnalysis,
    RevisionDraft,
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
def _hard_check(draft: dict, structured_data: dict) -> AgentOutcome | None:
    """Non-negotiable checks that run with or without a key.

    The target_ref must resolve and original_text must match the resume
    verbatim. Neither needs a model, and neither may be skipped.
    """
    ref = draft.get("target_ref", "")
    if not resume_ops.exists(structured_data, ref):
        return AgentOutcome(
            value=CriticVerdict(
                approved=False,
                notes=f"target_ref {ref} does not resolve",
                severity="fabrication",
            ),
            agent="critic",
            notes=["hard check: unresolvable target_ref"],
        )

    if resume_ops.resolve(structured_data, ref) != draft.get("original_text", ""):
        return AgentOutcome(
            value=CriticVerdict(
                approved=False,
                notes="original_text does not match the resume verbatim",
                severity="fabrication",
            ),
            agent="critic",
            notes=["hard check: original_text mismatch"],
        )
    return None


def _batch_payload(pending: list[tuple[int, str, dict]]) -> str:
    """One numbered block per draft.

    The originals are labelled and fenced rather than trailing off the end of
    the prompt: the previous layout ended with "Keywords the writer claims to
    have added: ..." and models were observed *continuing* that sentence
    instead of answering, which is a prompt bug wearing a parse error.
    """
    blocks = []
    for position, (_, actual, draft) in enumerate(pending, start=1):
        keywords = ", ".join(draft.get("keywords", []) or []) or "(none)"
        blocks.append(
            f"[{position}]\n"
            f"ORIGINAL: {actual}\n"
            f"PROPOSED: {draft.get('suggested_text', '')}\n"
            f"KEYWORDS THE WRITER CLAIMS TO HAVE ADDED: {keywords}"
        )
    header = (
        f"Check {len(pending)} proposed rewrite(s). "
        f"Return one verdict per numbered item.\n\n"
    )
    return header + "\n\n".join(blocks)


def review_batch(drafts: list[dict], structured_data: dict) -> list[AgentOutcome]:
    """Judge every draft, using a single LLM call for all of them.

    One call rather than one per draft because the free tier allows one request
    per minute per model per account: a six-draft run made as six calls is
    guaranteed to be throttled partway through, and a throttled critic silently
    becomes the rule engine.
    """
    results: list[AgentOutcome] = [None] * len(drafts)  # type: ignore[list-item]
    pending: list[tuple[int, str, dict]] = []

    for i, draft in enumerate(drafts):
        failed = _hard_check(draft, structured_data)
        if failed is not None:
            results[i] = failed
            continue
        pending.append((i, resume_ops.resolve(structured_data, draft["target_ref"]), draft))

    if not pending:
        return results

    if not llm.is_configured():
        # No key configured at all -- expected, not a degradation.
        for i, actual, draft in pending:
            results[i] = AgentOutcome(
                value=_rule_critique(actual, draft),
                agent="critic",
                notes=["rule-engine critique"],
            )
        return results

    try:
        parsed, call = llm.structured(
            task="critique",
            system=prompts.CRITIC,
            user=_batch_payload(pending),
            schema=CriticBatch,
            temperature=0.0,
        )
        by_index = {v.index: v for v in parsed.verdicts}
        expected = set(range(1, len(pending) + 1))
        if set(by_index) != expected:
            raise ValueError(
                f"critic returned verdicts {sorted(by_index)} for {len(pending)} draft(s)"
            )

        # One call's cost is shared; attributing it to every draft would
        # multiply the reported token spend by the batch size.
        for position, (i, _actual, _draft) in enumerate(pending, start=1):
            verdict = by_index[position]
            first = position == 1
            results[i] = AgentOutcome(
                value=CriticVerdict(
                    approved=verdict.approved,
                    notes=verdict.notes,
                    severity=verdict.severity,
                ),
                agent="critic",
                model=call.model,
                latency_ms=call.latency_ms if first else 0,
                status=call.status,
                tokens_in=call.tokens_in if first else 0,
                tokens_out=call.tokens_out if first else 0,
                used_llm=True,
                notes=(
                    [f"critic answered via {call.status} salvage, not json_schema"]
                    if call.status != "ok"
                    else []
                ),
            )
        return results

    except Exception as exc:  # noqa: BLE001
        # Degraded, not merely "no LLM". The rule critic checks three hardcoded
        # phrases, so an inflated metric ("8 teams" -> "20 teams") walks
        # straight through it. This must be visible, not just logged: silently
        # swapping the anti-fabrication gate for a substring match is the worst
        # failure this system has.
        logger.warning("Critic fell back to rules: %s", exc)
        for i, actual, draft in pending:
            results[i] = AgentOutcome(
                value=_rule_critique(actual, draft),
                agent="critic",
                status="degraded",
                notes=[f"critic LLM unavailable, rule engine used: {exc}"],
            )
        return results


def review_draft(draft: dict, structured_data: dict) -> AgentOutcome:
    """Judge a single draft.

    A one-element batch, so the single-draft path callers use and the batched
    path the tailor graph uses are the same code. The critic corpus therefore
    measures what actually ships.
    """
    return review_batch([draft], structured_data)[0]


_FABRICATION_HINTS = ("led a team of", "promoted to", "managed a budget")


def _rule_critique(original: str, draft: dict) -> CriticVerdict:
    suggested = draft.get("suggested_text", "")
    if not suggested.strip():
        return CriticVerdict(approved=False, notes="empty rewrite", severity="minor")

    # A new number that was not in the original is a fabricated metric.
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


# --------------------------------------------------- targeted finding rewrite
# Word-ish tokens for the deterministic fallback's keyword list.
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z+.#-]{2,}")


def rewrite_for_finding(
    structured_data: dict,
    target_ref: str,
    finding: dict,
    user_id: uuid.UUID | None = None,
    resume_id: uuid.UUID | None = None,
    store: VectorStore | None = None,
) -> AgentOutcome:
    """Draft a fix for ONE finding at ONE target_ref.

    Distinct from write_suggestions, which is JD-gap driven and drafts across
    every bullet. Here the driver is a finding, and there is exactly one target.

    Two prompt paths, because they are genuinely different jobs:

      revise    the ref holds text -> improve it without inventing anything.
      compose   the ref resolves to "" (summary.missing) -> write it from the
                experience the resume already contains. Feeding an empty
                original into a "improve this" prompt produces a summary
                written from nothing, which is the one thing the critic exists
                to stop.
    """
    original = resume_ops.resolve(structured_data, target_ref)
    composing = not original.strip()

    context = _grounding_context(
        structured_data, target_ref, user_id, resume_id, store, composing
    )

    if llm.is_configured():
        try:
            system = (
                prompts.COMPOSER.format(context=context)
                if composing
                else prompts.REVISER.format(context=context)
            )
            payload = (
                f"Problem to fix: {finding.get('message', '')}\n"
                f"Guidance: {finding.get('fix_hint', '')}\n\n"
            )
            payload += (
                "Write the missing section."
                if composing
                else f"Current text:\n{original}"
            )
            parsed, call = llm.structured(
                task="writing",
                system=system,
                user=payload,
                schema=RevisionDraft,
                temperature=0.4,
            )
            text = (parsed.suggested_text or "").strip()
            if text:
                return AgentOutcome(
                    value={
                        "target_ref": target_ref,
                        "original_text": original,
                        "suggested_text": text,
                        "keywords": [k for k in parsed.keywords if k][:8],
                        "reasoning": (parsed.reasoning or "").strip(),
                    },
                    agent="writer",
                    model=call.model,
                    latency_ms=call.latency_ms,
                    tokens_in=call.tokens_in,
                    tokens_out=call.tokens_out,
                    used_llm=True,
                )
            logger.warning("rewrite_for_finding: model returned empty text")
        except Exception as exc:  # noqa: BLE001
            logger.warning("rewrite_for_finding fell back to rules: %s", exc)
            return AgentOutcome(
                value=_rule_rewrite(original, finding, composing, structured_data),
                agent="writer",
                status="degraded",
                notes=[f"writer LLM unavailable, rule engine used: {exc}"],
            )

    return AgentOutcome(
        value=_rule_rewrite(original, finding, composing, structured_data),
        agent="writer",
        notes=["rule-engine rewrite"],
    )


def _grounding_context(
    structured_data: dict,
    target_ref: str,
    user_id: uuid.UUID | None,
    resume_id: uuid.UUID | None,
    store: VectorStore | None,
    composing: bool,
) -> str:
    """This candidate's own text, for tone (revise) or substance (compose).

    Composing a summary needs the whole resume, not five similar bullets --
    the summary has to be true of the document as a whole.
    """
    if composing:
        lines = [
            f"- {text}" for _ref, _placement, text in resume_ops.iter_bullets(structured_data)
        ][:12]
        roles = [
            f"- {e.get('role', '')} at {e.get('company', '')}".strip()
            for e in (structured_data.get("experience") or [])
        ][:6]
        block = "\n".join(roles + lines)
        return block or "(none)"

    if store is None or user_id is None:
        return "(none)"
    try:
        seed = resume_ops.resolve(structured_data, target_ref)
        hits = store.search(
            seed, corpus="resume_bullets", user_id=user_id, resume_id=resume_id, k=5
        )
        return _context_block(hits)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rewrite retrieval failed: %s", exc)
        return "(none)"


def _rule_rewrite(
    original: str, finding: dict, composing: bool, structured_data: dict
) -> dict:
    """Deterministic fallback. Honest rather than clever.

    It must never invent a fact, so it can only do mechanical things: swap a
    weak opener for a strong one, trim an overlong bullet at a clause boundary,
    or assemble a summary from role titles that are already in the document.
    The card labels this as written without AI.
    """
    keywords: list[str] = []
    text = original.strip()

    if composing:
        roles = [
            (e.get("role") or "").strip()
            for e in (structured_data.get("experience") or [])
            if (e.get("role") or "").strip()
        ]
        skills = [
            item
            for group in (structured_data.get("skills") or [])
            for item in (group.get("items") or [])
        ][:4]
        lead = roles[0] if roles else "Professional"
        # No counts. A number here is a number the original text did not have,
        # and _rule_critique correctly reads that as an invented metric -- the
        # fallback would fabricate its way straight into a 422.
        tail = f" with hands-on experience across {', '.join(skills)}." if skills else "."
        text = f"{lead} focused on practical delivery{tail}"
        keywords = skills
        return {
            "target_ref": "",
            "original_text": original,
            "suggested_text": text,
            "keywords": keywords,
            "reasoning": (
                "Assembled from the roles and skills already in your resume. "
                "Written without AI - edit it before accepting."
            ),
        }

    fid = finding.get("id", "")
    reason = "Mechanical fix applied without AI."

    if fid.endswith("weak_verbs") or fid.endswith("no_action_verb"):
        words = text.split()
        if words:
            swapped = _STRONGER.get(words[0].lower())
            if swapped:
                words[0] = swapped if text[:1].islower() else swapped.capitalize()
                text = " ".join(words)
                keywords = [words[0]]
                reason = f"Opened with a stronger verb ({words[0]})."
    elif fid.endswith("overlong_bullets"):
        parts = re.split(r",\s+", text)
        if len(parts) > 1:
            trimmed = parts[0].rstrip(".")
            if len(trimmed.split()) >= 6:
                text = trimmed + "."
                reason = "Trimmed to the first clause to get under the length cap."

    return {
        "target_ref": "",
        "original_text": original,
        "suggested_text": text,
        "keywords": keywords,
        "reasoning": reason + " Written without AI - review before accepting.",
    }


_STRONGER = {
    "worked": "delivered",
    "helped": "drove",
    "assisted": "supported",
    "responsible": "owned",
    "handled": "managed",
    "did": "executed",
    "made": "built",
    "used": "applied",
    "participated": "contributed",
    "involved": "led",
}



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
