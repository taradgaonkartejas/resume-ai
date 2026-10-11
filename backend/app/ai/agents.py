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
    AtsReview,
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
from app.services import ats as ats_rules
from app.services import resume_ops
from app.services.heuristics import (
    BULLET_COACHED_MAX,
    extract_keywords,
    score_resume,
)

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
def _notes_or(notes, fallback: dict) -> dict:
    """Per-category prose, falling back per key rather than wholesale.

    `category_notes` used to be a dict and `parsed.category_notes or
    fallback` was enough. It is a model now -- Groq's strict json_schema
    mode rejects open-ended maps -- and a model is ALWAYS truthy, so that
    expression would have quietly shipped the model object and never once
    used the fallback. Falling back per key is also better than all-or-
    nothing: a model that writes three good notes and omits the fourth
    should keep the three.
    """
    written = {k: v.strip() for k, v in notes.model_dump().items() if v and v.strip()}
    return {**fallback, **written}


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
                    "category_notes": _notes_or(
                        parsed.category_notes, fallback_notes
                    ),
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
    rejections: list[dict] | None = None,
) -> AgentOutcome:
    """Draft rewrites. Retrieval is scoped to this user's own bullets.

    `rejections` carries the critic's notes from the previous round. Until
    now a revision round re-asked the identical question at the same
    temperature and hoped for a different answer, which is why retrying was
    restricted to the case where every single draft had been rejected.
    """
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
            if rejections:
                detail = "\n".join(
                    f"- {r.get('target_ref', '?')}: {r.get('critic_notes') or 'rejected'}"
                    for r in rejections[:6]
                )
                payload += (
                    "\n\nThese drafts were REJECTED in the previous round. "
                    "Do not repeat them:\n"
                    f"{detail}\n"
                    "Keep every figure and unit that the original text "
                    "already contained, and invent none."
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
# Numbers a reader takes as a quantitative CLAIM: "58", "22", "9", "3.5".
# The lookbehind excludes digits glued to the end of a word -- "S3", "EC2",
# "v2", "OAuth2" are names, not measurements, and flagging them as fabricated
# metrics would be noise. A digit followed by letters stays in scope, because
# "3x faster" and "5k users" are very much claims.
_CLAIM_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?")

# What counts as already-present in the resume. Slightly more permissive than
# the claim pattern, but not blindly so: a MULTI-digit run inside a token is
# usually a real figure ("FY2019"), whereas a single digit inside a token is
# almost always part of a name. Letting "S3" license the claim "3x faster"
# would have made the guard useless for small numbers, which is precisely
# where fabrication is easiest.
_ANY_NUMBER_RE = re.compile(r"\d{2,}(?:\.\d+)?")


def unverifiable_numbers(resume_text: str, suggested: str) -> set[str]:
    """Numbers in `suggested` that appear nowhere in the resume.

    Compared against the WHOLE resume rather than the one string being
    rewritten, which is the difference between a guard that works and one that
    breaks the compose path. Writing a summary from an empty string must be
    allowed to surface "22 minutes to 9 minutes" from a bullet below it --
    those are the candidate's own facts. Only a number with no source anywhere
    in the document is a fabrication.

    Digits only, on purpose. A number is either present in the source text or
    it is not, so this has no judgement calls and no false positives. Semantic
    fabrication -- "contributed to" becoming "Led" -- is the LLM critic's job
    and no regex will ever catch it.
    """
    known = set(_CLAIM_NUMBER_RE.findall(resume_text))
    known |= set(_ANY_NUMBER_RE.findall(resume_text))
    # Normalise 9 and 9.0 to the same fact so formatting is not a fabrication.
    known |= {n.rstrip("0").rstrip(".") for n in known if "." in n}
    found = set(_CLAIM_NUMBER_RE.findall(suggested))
    return {
        n for n in found
        if n not in known and (n.rstrip("0").rstrip(".") if "." in n else n) not in known
    }


def _self_check(draft: dict, structured_data: dict) -> str | None:
    """Why this draft would be rejected, phrased for the writer to act on.

    Deliberately the same two deterministic rules the critic's hard checks
    apply, so the retry prompt cannot drift away from what is actually
    enforced downstream.
    """
    invented = unverifiable_numbers(
        resume_ops.to_plain_text(structured_data),
        draft.get("suggested_text", ""),
    )
    if invented:
        return (
            "it states "
            + ", ".join(str(n) for n in invented)
            + ", which appears nowhere in the resume. Use only figures the "
            "resume already contains, or none at all"
        )

    regressions = introduced_findings(
        structured_data, draft.get("target_ref", ""),
        draft.get("suggested_text", ""),
    )
    if regressions:
        return (
            "it fixes the stated problem but introduces "
            + ", ".join(regressions)
            + " -- most often by dropping a number, a unit or a strong verb "
            "that the original text carried"
        )
    return None


def introduced_findings(
    structured_data: dict, target_ref: str, suggested: str
) -> list[str]:
    """Findings this rewrite would CREATE that the resume does not have yet.

    Fixing one problem by causing another is not a fix. The writer corpus has
    measured this as its `clean` column from the beginning -- and production
    never enforced it, so the corpus held the writer to a standard the
    application did not.

    Measured, reproducibly, across two consecutive live runs: asked to shorten
    an overlong padded bullet, the writer removed the clause that carried the
    metric, clearing format.overlong_bullets and raising
    experience.unquantified_bullets in the same edit.

    Deterministic and local -- it is the same scorer the UI shows -- so it
    costs no tokens and works on the rule path and the model path alike. The
    earlier fix for this only touched _rule_rewrite, which the live writer
    never calls.
    """
    if not suggested.strip():
        return []
    try:
        composing = not resume_ops.resolve(structured_data, target_ref).strip()
        before_findings = score_resume(structured_data)["findings"]
        before = {f["id"] for f in before_findings}
        patched = resume_ops.apply_patch(structured_data, target_ref, suggested)
        after_findings = score_resume(patched)["findings"]
    except Exception:  # noqa: BLE001 - a guard must never be the thing that breaks
        logger.warning("introduced_findings could not score %s", target_ref)
        return []

    new_ids = [f for f in after_findings if f["id"] not in before]

    if composing:
        # A section that did not exist cannot regress. summary.missing
        # SUPPRESSES the other summary rules, so composing any summary at all
        # unmasks summary.too_short, summary.no_metric and
        # summary.no_action_verb -- by construction, not because the writer
        # did anything wrong.
        #
        # And summary.no_metric can only be cleared by a number, which the
        # writer is forbidden to invent. Counting these would make the compose
        # path impossible to satisfy: every draft rejected, /rewrite
        # permanently 422. Findings at OTHER refs still count.
        new_ids = [f for f in new_ids if f.get("target_ref") != target_ref]

    return sorted(f["id"] for f in new_ids)


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

    # A number with no source anywhere in the resume is a fabricated metric.
    #
    # This runs here, in the checks that happen "with or without a key",
    # because until now the ONLY thing between an invented figure and the
    # user's resume was a single LLM call. Measured: on the compose path the
    # writer produced "58" for a resume whose nearest real figure is a 59.1%
    # reduction. The LLM critic did reject it -- but that is a probabilistic
    # guard on this project's most important safety property, and it costs
    # nothing to put a deterministic one in front of it.
    regressions = introduced_findings(
        structured_data,
        draft.get("target_ref", ""),
        draft.get("suggested_text", ""),
    )
    if regressions:
        return AgentOutcome(
            value=CriticVerdict(
                approved=False,
                notes=(
                    "the rewrite introduces "
                    + ", ".join(regressions)
                    + ", which the resume did not have before"
                ),
                severity="regression",
            ),
            agent="critic",
            notes=[f"hard check: introduces {', '.join(regressions)}"],
        )

    invented = unverifiable_numbers(
        resume_ops.to_plain_text(structured_data), draft.get("suggested_text", "")
    )
    if invented:
        return AgentOutcome(
            value=CriticVerdict(
                approved=False,
                notes=(
                    "introduces number(s) that appear nowhere in the resume: "
                    + ", ".join(sorted(invented))
                ),
                severity="fabrication",
            ),
            agent="critic",
            notes=["hard check: unverifiable number"],
        )
    return None


def _batch_payload(
    pending: list[tuple[int, str, dict]], structured_data: dict | None = None
) -> str:
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
    # The resume itself, once, ahead of the drafts.
    #
    # Until now the critic was shown ONLY the single line being rewritten, and
    # then asked whether the rewrite claimed anything "the original" did not
    # support. For a composed summary the ORIGINAL is empty, so rejection was
    # the only answer consistent with what it had been given -- the critic was
    # not being strict, it was being asked an impossible question. Measured:
    # summary rewrites rejected in run after run, and one live rejection read
    # "Adds technology (Go) not present in original" for a resume that lists
    # Go under skills.
    #
    # The deterministic half of the same safety system already compares
    # against the whole resume (unverifiable_numbers uses to_plain_text), so
    # the two halves disagreed about what the source even was.
    resume_block = ""
    if structured_data:
        try:
            full = resume_ops.to_plain_text(structured_data).strip()
        except Exception:  # noqa: BLE001 - never let context-building fail a review
            full = ""
        if full:
            resume_block = f"RESUME (reference -- for checking whether a fact is real):\n{full}\n\n"

    header = (
        f"Check {len(pending)} proposed rewrite(s). "
        f"Return one verdict per numbered item.\n\n"
    )
    return resume_block + header + "\n\n".join(blocks)


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
            user=_batch_payload(pending, structured_data),
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

            # Up to two attempts. The writer has never been told why a draft
            # was thrown away: a rejected rewrite was simply discarded and the
            # endpoint answered 422. Both deterministic rejections -- an
            # invented number, and a rewrite that introduces a new finding --
            # describe exactly what is wrong in a sentence the model can act
            # on, so the second attempt is given that sentence rather than
            # being asked the same question again.
            attempt_payload = payload
            last: tuple[dict, object] | None = None
            for attempt in (1, 2):
                parsed, call = llm.structured(
                    task="writing",
                    system=system,
                    user=attempt_payload,
                    schema=RevisionDraft,
                    temperature=0.4,
                )
                text = (parsed.suggested_text or "").strip()
                if not text:
                    logger.warning("rewrite_for_finding: model returned empty text")
                    break

                value = {
                    "target_ref": target_ref,
                    "original_text": original,
                    "suggested_text": text,
                    "keywords": [k for k in parsed.keywords if k][:8],
                    "reasoning": (parsed.reasoning or "").strip(),
                }
                last = (value, call)

                complaint = _self_check(value, structured_data)
                if complaint is None or attempt == 2:
                    # Attempt 2 is returned even when it still fails: the
                    # critic and the same hard checks run again downstream and
                    # will reject it. Swallowing it here would turn a visible
                    # rejection into a silent one.
                    return AgentOutcome(
                        value=value,
                        agent="writer",
                        model=call.model,
                        latency_ms=call.latency_ms,
                        tokens_in=call.tokens_in,
                        tokens_out=call.tokens_out,
                        used_llm=True,
                        notes=[] if complaint is None else [f"retry still {complaint}"],
                    )

                logger.info("rewrite_for_finding retrying: %s", complaint)
                attempt_payload = (
                    f"{payload}\n\n"
                    f"Your previous attempt was REJECTED because {complaint}.\n"
                    f"Rejected attempt:\n{text}\n\n"
                    "Write a new version that fixes the stated problem and "
                    "keeps every fact, figure and unit already present."
                )

            if last is not None:
                value, call = last
                return AgentOutcome(
                    value=value, agent="writer", model=call.model,
                    latency_ms=call.latency_ms, tokens_in=call.tokens_in,
                    tokens_out=call.tokens_out, used_llm=True,
                )
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
            # Keeping only the first clause is what made
            # bullet_overlong_and_padded fix one finding and raise another:
            # the padding that made the bullet too long was also carrying the
            # metric, so trimming produced experience.unquantified_bullets.
            # Walk outwards instead and take the SHORTEST prefix that is long
            # enough, short enough, and still carries a number if the original
            # had one.
            had_digit = bool(re.search(r"\d", text))
            chosen = None
            for cut in range(1, len(parts)):
                candidate = ", ".join(parts[:cut]).rstrip(".")
                words = candidate.split()
                if len(words) < 6 or len(words) > BULLET_COACHED_MAX:
                    continue
                if had_digit and not re.search(r"\d", candidate):
                    continue
                chosen = candidate
                break
            if chosen is not None:
                text = chosen + "."
                reason = (
                    "Trimmed to the shortest clause that stays under the "
                    "length cap without dropping the metric."
                )

    return {
        "target_ref": "",
        "original_text": original,
        "suggested_text": text,
        "keywords": keywords,
        "reasoning": reason + " Written without AI - review before accepting.",
    }


# A rule rewrite has to clear two bars, and the original map cleared both on
# only three of its ten entries.
#
#   FAITHFUL -- it must not claim more than the original said. Three entries
#   promoted a supporting role into a leading one:
#       worked   -> delivered   "worked on X" is not "delivered X"
#       helped   -> drove       helping is not driving
#       involved -> led         involvement is not leadership
#   Those are fabrications. The numeric guard cannot see them -- it only reads
#   digits -- so unlike the model's inventions, which the guard catches every
#   time, these shipped. They were produced deterministically by code we own,
#   which makes them the worst kind in a project whose stated top invariant is
#   zero fabrications.
#
#   EFFECTIVE -- the replacement must actually be an ACTION_VERB, or the
#   finding stays open and the user is shown a suggestion that changes their
#   wording and improves nothing:
#       assisted     -> supported     not an action verb
#       did          -> executed      not an action verb
#       used         -> applied       not an action verb
#       participated -> contributed   not an action verb
#
# What survives is the intersection. Where no faithful, effective swap exists,
# the rule engine now declines -- which is the correct answer, and the one the
# corpus already rewards over a cosmetic edit.
_STRONGER = {
    "responsible": "owned",   # "responsible for X" does mean ownership of X
    "handled": "managed",
    "made": "built",
}



# ----------------------------------------------------------------- ats agent


def review_ats(
    structured_data: dict, store: VectorStore | None = None
) -> AgentOutcome:
    """Judgement calls about machine-readability. Advisory, never scored.

    The deterministic checks in `services/ats.py` own every point this
    feature is worth. This agent exists for the one question that rules
    genuinely cannot answer -- what does this acronym stand for -- and its
    output is presented as tips, not findings.

    It used to answer a second question (is this oddly-named section really
    the work history?). That half failed its pre-registered false-positive
    gate on a real resume and was deleted; see `_rule_ats_review`.

    Keeping it out of the score is the whole design, not a limitation. A
    score that moved because a model felt differently on a Tuesday would be
    unreproducible, unexplainable, and impossible to test; the same reason
    ScoringAgent is forbidden from touching the number.
    """
    acronyms = ats_rules.bare_acronyms(structured_data)

    # The gate. No question, no call -- a tidy resume costs nothing.
    if not acronyms:
        return AgentOutcome(
            value={"expansions": [], "note": ""},
            agent="ats",
            notes=["no candidates"],
        )

    if not llm.is_configured():
        return AgentOutcome(
            value=_rule_ats_review(),
            agent="ats",
            notes=["rule-engine ats review"],
        )

    context = "(none)"
    if store is not None:
        try:
            hits = store.search(
                "ats section headings parsing acronyms", corpus="ats_rules", k=4
            )
            context = _context_block(hits)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ATS retrieval failed: %s", exc)

    payload_parts = ["Skills written only as an acronym:\n" + ", ".join(acronyms)]

    try:
        parsed, call = llm.structured(
            task="ats",
            system=prompts.ATS_REVIEW.format(context=context),
            user="\n\n".join(payload_parts),
            schema=AtsReview,
            temperature=0.2,
        )
        return AgentOutcome(
            value=_clean_ats_review(parsed, acronyms),
            agent="ats",
            model=call.model,
            latency_ms=call.latency_ms,
            tokens_in=call.tokens_in,
            tokens_out=call.tokens_out,
            used_llm=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ATS agent fell back to rules: %s", exc)

    return AgentOutcome(
        value=_rule_ats_review(),
        agent="ats",
        notes=["rule-engine ats review"],
    )


def _clean_ats_review(parsed: AtsReview, acronyms: list[str]) -> dict:
    """Keep only what the model was actually asked about.

    The same discipline as `_hard_check` on the writer path, for the same
    reason: every fabrication we have ever caught in this codebase was caught
    by a deterministic guard, never by a model policing itself. Here that
    means an expansion must be of an acronym that is really on the resume.
    """
    allowed = {a.upper() for a in acronyms}
    expansions = []
    for item in parsed.expansions or []:
        token = (item.acronym or "").strip().upper()
        full = (item.expanded or "").strip()
        # An "expansion" that does not contain the acronym and is not longer
        # than it is not an expansion.
        if token in allowed and len(full) > len(token) + 3:
            expansions.append(
                {"acronym": token, "expanded": f"{full} ({token})" if token not in full else full}
            )

    return {
        "expansions": expansions,
        "note": (parsed.note or "").strip()[:400],
    }


def _rule_ats_review() -> dict:
    """No model available: say nothing. The remaining half needs judgement.

    This function once had a twin for section relabelling -- spotting that
    "My Journey" is really the work history and a parser matching on the word
    "Experience" will miss it. That half has been DELETED, on evidence.

    The rule version went first. It flagged

        "Leadership & Achievements"  ->  rename to a standard heading

    on a real resume, where that is a genuine leadership-and-achievements
    section that should keep its name. The detectable shape -- dated entries,
    employer-shaped text -- is identical for "My Journey" (a serious parse
    problem) and for that one (fine as it is). Telling them apart IS the
    judgement call, so the work moved to the agent.

    The agent then scored, on a pre-registered corpus with a holdout:

        recall on positives       100%
        FALSE POSITIVES           1/6 = 17%   (kill criterion: max 10%)
        correct target when fired 100%

    and the single false positive was that same "Leadership & Achievements"
    section, on 3 runs out of 3. The threshold was registered BEFORE the run
    precisely so this could not be argued away afterwards, and the measured
    rate is a floor: a rate-limited call degrades to this function, returns
    nothing, and scores as a correct negative.

    Recall was perfect. The feature worked when it should fire. It was
    deleted anyway, because the baseline it had to beat is silence, and a
    tool that tells you to rename a section you deliberately wrote is worse
    than no tool -- you have to notice it is wrong and undo it. 100% recall
    does not buy the right to be confidently wrong about a real resume.

    Acronym expansion survives: it scored 6 correct, 0 wrong, 0 fabricated,
    and it is checkable against the resume text, which relabelling is not.

    The deterministic ATS findings in services/ats.py are unaffected and
    still score.
    """
    return {"expansions": [], "note": ""}
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
