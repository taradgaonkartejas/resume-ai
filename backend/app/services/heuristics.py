"""Deterministic ATS scoring. Code, never an LLM — identical input, identical score.

Weights (DESIGN.md §6.6): Contact 15 / Summary 20 / Experience 45 / Format 20.

Every check emits a `Finding` carrying the points it is worth. That number is
not decorative: the guided editor prints "+3 points" on the card, so the score
MUST rise by exactly that much when the user fixes it. The invariant is
enforced by tests (fix the field, re-score, assert the delta), because a score
that lies is worse than showing no number at all.

To keep the invariant true, each scorer computes `points` as
`max_achievable - awarded` for the specific check it just ran.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

from app.services import ats, quality_rules
from app.services.resume_ops import date_label, to_plain_text
from app.services.text_match import canonical_aliases, contains, count_hits

WEIGHTS = {"contact": 15, "summary": 20, "experience": 45, "format": 20}

# Every check that can award points, and what it is worth.
#
# This exists because of Part 3. `WEIGHTS` sums to 100 and `Δscore == points`
# is asserted in three places, so a NEW scored finding cannot add to the
# total -- it must take its points from inside its own category. The six
# Tier 1 writing-quality checks therefore re-sliced `summary` and
# `experience` rather than extending them:
#
#   summary     length 8 -> 6, action_verb 6 -> 5, freeing 3 for filler
#   experience  base 10 -> 6, metric 18 -> 15, action_verb 12 -> 9,
#               dates 5 -> 3, freeing 12 for the five bullet-quality checks
#
# Scores computed before this change will move. That is a release note, not
# a bug.
#
# The scorers read these numbers instead of repeating them, and
# `test_score_budgets.py` asserts each category sums to its weight -- the
# guard Part 3 called for, which did not exist while the budgets were
# literals scattered through the file.
SUB_BUDGETS: dict[str, dict[str, int]] = {
    "contact": {
        "name": 4, "phone": 3, "location": 2, "email": 4, "links": 2,
    },
    "summary": {
        "length": 6, "metric": 6, "action_verb": 5, "filler": 3,
    },
    "experience": {
        "base": 6, "metric": 15, "action_verb": 9, "dates": 3,
        "filler": 3, "impossible_metric": 3, "first_person": 2,
        "repeated_opener": 2, "tense_mismatch": 2,
    },
    "format": {
        "skills": 4, "education": 3, "length": 5, "bullet_length": 4,
        "ats_dates": 2, "ats_glyphs": 1, "ats_order": 1,
    },
}

# Two different numbers on purpose, and now stated in one place instead of
# contradicting each other across heuristics.py, corpora.py and prompts.py:
#   COACHED  - what we advise and what the Writer aims for.
#   HARD_CAP - where the rule engine actually deducts points.
# Coaching a tighter target than we penalise is deliberate; doing it silently
# was the bug.
BULLET_COACHED_MAX = 30
BULLET_HARD_CAP = 45

# Past-tense and simple forms. A bullet is scored on its FIRST word, so this
# is the set that matters there.
_ACTION_VERBS_PAST = {
    # Leadership / ownership
    "led", "owned", "drove", "directed", "headed", "spearheaded", "championed",
    "coordinated", "oversaw", "managed", "mentored", "coached", "guided",
    # Building / creating
    "built", "rebuilt", "created", "designed", "developed", "engineered",
    "architected", "implemented", "launched", "shipped", "delivered",
    "deployed", "established", "founded", "introduced", "produced",
    "authored", "wrote", "prototyped",
    # Improving / changing
    "improved", "optimised", "optimized", "refactored", "modernised",
    "modernized", "streamlined", "simplified", "consolidated", "standardised",
    "standardized", "restructured", "rearchitected", "migrated", "upgraded",
    "orchestrated",
    "automated", "hardened", "secured", "transformed", "tuned", "ported",
    # Results
    "reduced", "cut", "eliminated", "saved", "accelerated", "scaled", "grew",
    "expanded", "increased", "generated", "recovered", "fixed", "resolved",
    # Analysis / collaboration
    "analysed", "analyzed", "measured", "evaluated", "forecast", "modeled",
    "modelled", "researched", "investigated", "diagnosed", "identified",
    "negotiated", "partnered", "collaborated", "presented", "influenced",
    "ran", "trained",
}

# Present participles. The list used to be past-tense only, so a summary
# reading "Platform engineer with 6 years BUILDING distributed systems" scored
# no action verb and raised summary.no_action_verb -- a finding the user could
# not act on, and one the live writer kept tripping while fixing something
# else. These are not derivable: led->leading, built->building, ran->running,
# cut->cutting are all irregular, so they are listed.
_ACTION_VERBS_PARTICIPLE = {
    "leading", "owning", "driving", "directing", "heading", "spearheading",
    "coordinating", "overseeing", "managing", "mentoring", "coaching",
    "guiding", "building", "rebuilding", "creating", "designing",
    "developing", "engineering", "architecting", "implementing", "launching",
    "shipping", "delivering", "deploying", "establishing", "founding",
    "introducing", "producing", "authoring", "writing", "prototyping",
    "improving", "optimising", "optimizing", "refactoring", "modernising",
    "modernizing", "streamlining", "simplifying", "consolidating",
    "standardising", "standardizing", "restructuring", "rearchitecting",
    "orchestrating",
    "migrating", "upgrading", "automating", "hardening", "securing",
    "transforming", "tuning", "porting", "reducing", "cutting", "eliminating",
    "saving", "accelerating", "scaling", "growing", "expanding", "increasing",
    "generating", "recovering", "fixing", "resolving", "analysing",
    "analyzing", "measuring", "evaluating", "forecasting", "modeling",
    "modelling", "researching", "investigating", "diagnosing", "identifying",
    "negotiating", "partnering", "collaborating", "presenting", "influencing",
    "running", "training",
}

# Present-tense forms, added when Part 3 introduced `experience.tense_mismatch`.
# Without them a bullet reading "Manage a team of five" was penalised TWICE
# for one defect: once by `weak_verbs` ("does not start with an action verb",
# which is false -- "Manage" is an action verb) and again by the new tense
# rule. Worse, in a CURRENT role, where present tense is correct, only the
# false accusation fired. Each rule now owns exactly one thing: this set
# decides whether the opener is a strong verb, and the tense rule decides
# whether it is in the right tense.
ACTION_VERBS = _ACTION_VERBS_PAST | _ACTION_VERBS_PARTICIPLE

# Present-tense verbs are credited ONLY as the first word of a bullet, never
# anywhere in a summary.
#
# Part 3 introduced `experience.tense_mismatch`, and without present-tense
# forms a bullet reading "Manage a team of five" was penalised TWICE for one
# defect: once by `weak_verbs` ("does not start with an action verb", which
# is false -- "Manage" IS an action verb) and again by the tense rule. In a
# CURRENT role, where present tense is correct, only the false accusation
# fired.
#
# Merging them into ACTION_VERBS outright was wrong, and a test caught it:
# the summary check asks `contains(text, verb)` over the WHOLE string, so
# "engineer" as a verb credited every resume reading "Platform engineer".
# Most present-tense verbs are also common nouns -- design, lead, monitor,
# support, review, scale, partner, test, work. At position 0 of a bullet
# they are unambiguously verbs; mid-sentence they are not. So the wider set
# is used only where position does the disambiguating.
BULLET_OPENER_VERBS = ACTION_VERBS | quality_rules.PRESENT_TENSE_OPENERS

# A number only counts as quantification when something is actually being
# measured. The old pattern included a bare `\b\d{2,}\b`, so "shipped it in
# 2023" scored as a quantified achievement. Every branch below now requires a
# unit, a currency, a multiplier, or a following word -- and year-shaped values
# are explicitly excluded from the number+noun branch.
_METRIC_RE = re.compile(
    r"""(
          \d+(?:\.\d+)?\+?\s*%                       # 40%   3.5 %   20+%
        | [$£€]\s?\d                                 # $1.2M
        | \b\d+(?:\.\d+)?\+?\s*[kmb]\b               # 40k   1.2M   10k+
        | \bx\s?\d+(?:\.\d+)?\b                      # x3
        | \b\d+(?:\.\d+)?\s?x\b                      # 3x
        | \b\d+(?:\.\d+)?\+?\s*(?:ms|s|sec|secs|seconds|min|mins|minutes
                              |hr|hrs|hours|days|weeks|months|years)\b
        | \b(?!(?:19|20)\d{2}\b)\d+(?:\.\d+)?\+?\s+\w+   # 8 teams, 10+ engineers
    )""",
    re.IGNORECASE | re.VERBOSE,
)
# --- tenure is not an achievement -------------------------------------------
#
# "5+ years of experience" is the most common opening line in a resume
# summary, and widening _METRIC_RE to accept the N+ form made it satisfy
# summary.no_metric on every resume in the corpus at once -- the finding
# stopped firing not because the summaries improved but because they all
# state a duration.
#
# The resolution is not to pick a side. Tenure and achievement are two
# different signals that happen to look alike to a regex:
#
#   * To an ATS, tenure is a SCREENING FIELD. Workday, Taleo, SuccessFactors
#     and iCIMS parse years-of-experience and compare it against the
#     requisition's stated minimum, and recruiters filter on it directly. A
#     summary that omits it can be screened out before a human reads a word,
#     so the number earns its place and must never be flagged as clutter.
#
#   * To the human reading the shortlist at an MNC, tenure is an INPUT, not an
#     outcome. "4+ years of Java" reports that time passed. The summary is
#     three lines of prime real estate and the question it must answer is
#     "what moved" -- percent, money, time, scale.
#
# So tenure keeps matching _METRIC_RE everywhere else (an experience bullet
# reading "2+ years leading the migration" is still quantified), and only the
# summary's achievement test runs against the text with tenure spans removed.
_TENURE_RE = re.compile(
    r"""\b
    (?:over|nearly|almost|about|around|approx\.?|approximately|~)?\s*
    \d+(?:\.\d+)?\s*\+?\s*
    (?:years?|yrs?)
    (?:\s*\+)?
    \b""",
    re.IGNORECASE | re.VERBOSE,
)
# A duration can BE the achievement -- "cut onboarding to 2 years" is a
# result, not a tenure claim. Matched against the text immediately preceding
# the duration so a real outcome is never stripped.
_ACHIEVED_DURATION_RE = re.compile(
    r"\b(?:by|from|to|within|under|below|saving|saved|cut|cutting|reduced"
    r"|reducing|ahead\s+of|every)\s*$",
    re.IGNORECASE,
)
# The marker can also TRAIL the duration: "delivered 2 years ahead of
# schedule" puts the achievement word after the number, so one-sided context
# is not enough.
_ACHIEVED_DURATION_AFTER_RE = re.compile(
    r"^\s*(?:ahead\s+of\s+schedule|early|earlier|sooner|faster|late"
    r"|of\s+(?:manual\s+)?(?:effort|work|time|toil)\s+saved|under\s+budget)",
    re.IGNORECASE,
)


def _strip_tenure(text: str) -> str:
    """`text` with tenure claims removed, leaving achievement metrics intact."""
    out: list[str] = []
    last = 0
    for m in _TENURE_RE.finditer(text):
        before = text[max(0, m.start() - 24):m.start()].rstrip()
        after = text[m.end():m.end() + 28]
        if _ACHIEVED_DURATION_RE.search(before) or _ACHIEVED_DURATION_AFTER_RE.search(after):
            continue
        out.append(text[last:m.start()])
        last = m.end()
    out.append(text[last:])
    return "".join(out)


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class Finding:
    """One actionable improvement.

    `id` is stable across runs so the UI can remember dismissals; it must not
    embed indices that shift when the user reorders entries, EXCEPT where the
    finding genuinely belongs to one entry (then the index is part of it).

    `target_ref` uses the grammar in resume_ops so the editor can scroll to and
    focus the exact field. `points` is what fixing it adds to the score.
    """

    id: str
    category: str
    target_ref: str
    severity: str  # "high" | "medium" | "low"
    points: int
    message: str
    fix_hint: str
    # Populated for findings the user can act on with one click.
    action: str = ""  # "" | "rewrite" | "rerank" | "quantify"
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _sev(points: int) -> str:
    """Severity follows the score impact, so the two can never disagree."""
    if points >= 5:
        return "high"
    if points >= 3:
        return "medium"
    return "low"


# --------------------------------------------------------------- contact ---
def _score_contact(data: dict) -> tuple[int, list[Finding]]:
    contact = data.get("contact", {}) or {}
    findings: list[Finding] = []
    points = 0

    # (key, worth, target_ref, message, hint)
    checks = [
        ("name", 4, "contact.name", "Missing name",
         "Your full name should be the first thing on the resume."),
        ("phone", 3, "contact.phone", "Missing phone number",
         "Recruiters often call before they email. Add a reachable number."),
        ("location", 2, "contact.location", "Missing location",
         "Add at least city and country — many ATS filters sort on location."),
    ]
    for key, worth, ref, msg, hint in checks:
        if contact.get(key):
            points += worth
        else:
            findings.append(Finding(
                id=f"contact.{key}.missing", category="contact", target_ref=ref,
                severity=_sev(worth), points=worth, message=msg, fix_hint=hint,
            ))

    email = contact.get("email", "") or ""
    if email and _EMAIL_RE.match(email):
        points += 4
    else:
        findings.append(Finding(
            id="contact.email.invalid", category="contact",
            target_ref="contact.email", severity=_sev(4), points=4,
            message="Missing or malformed email" if email else "Missing email",
            fix_hint="Use a professional address you actually check.",
        ))

    if contact.get("links"):
        points += 2
    else:
        findings.append(Finding(
            id="contact.links.missing", category="contact",
            target_ref="contact.links", severity=_sev(2), points=2,
            message="No profile links",
            fix_hint="Add LinkedIn, GitHub or a portfolio — it is free credibility.",
        ))

    return min(points, WEIGHTS["contact"]), findings


# --------------------------------------------------------------- summary ---
def _score_summary(data: dict) -> tuple[int, list[Finding]]:
    text = (data.get("summary", {}).get("text") or "").strip()
    findings: list[Finding] = []

    if not text:
        return 0, [Finding(
            id="summary.missing", category="summary", target_ref="summary.text",
            severity="high", points=WEIGHTS["summary"],
            message="No professional summary",
            fix_hint="Three lines: your role, your years, and your strongest result.",
            action="rewrite",
        )]

    budget = SUB_BUDGETS["summary"]
    words = len(text.split())
    length_max = budget["length"]
    length_points = length_max if words >= 25 else (4 if words >= 12 else 1)
    points = length_points
    if length_points < length_max:
        findings.append(Finding(
            id="summary.too_short", category="summary", target_ref="summary.text",
            severity=_sev(length_max - length_points),
            points=length_max - length_points,
            message=f"Summary is short ({words} words) — aim for 30–60",
            fix_hint="Name the role you want and quantify one achievement.",
            action="rewrite", meta={"words": words},
        ))

    # Judged on the summary with tenure removed: see _TENURE_RE above.
    if _METRIC_RE.search(_strip_tenure(text)):
        points += budget["metric"]
    else:
        # Distinguish "no numbers at all" from "a duration and nothing else".
        # The advice differs, and telling someone who wrote "5+ years of
        # experience" that their summary "has no quantified achievement"
        # reads as though the system cannot see the number they typed.
        tenure_only = bool(_METRIC_RE.search(text))
        findings.append(Finding(
            id="summary.no_metric", category="summary", target_ref="summary.text",
            severity=_sev(budget["metric"]), points=budget["metric"],
            message=(
                "Summary states years of experience but no quantified achievement"
                if tenure_only else
                "Summary has no quantified achievement"
            ),
            fix_hint=(
                "Keep the years — an ATS screens on them. Add one outcome "
                "next to it: % improved, time saved, money, or scale."
                if tenure_only else
                "Add a real number — team size, users served, latency cut."
            ),
            # `quantify`, NOT `rewrite`. The number still has to come from the
            # user -- inventing a metric is the one thing this system must
            # never do, and the writer corpus marks this finding must-decline.
            #
            # What changed is that refusing is no longer the only honest
            # answer. The writer restructures the line so a figure FITS and
            # leaves a visible [__] for the user to fill in, which asserts
            # nothing. A plain `rewrite` here would still promise a fix the
            # system is built never to give.
            action="quantify",
            meta={"tenure_only": tenure_only},
        ))

    # Whole-word, not substring: "Fulfilled orders" used to earn action-verb
    # credit because "fulfilled" contains "led", and "Downed" contains "owned".
    if any(contains(text, v) for v in ACTION_VERBS):
        points += budget["action_verb"]
    else:
        findings.append(Finding(
            id="summary.no_action_verb", category="summary",
            target_ref="summary.text",
            severity=_sev(budget["action_verb"]), points=budget["action_verb"],
            message="Summary lacks a strong action verb",
            fix_hint="Open with a verb like 'Led', 'Built' or 'Scaled'.",
            action="rewrite",
        ))

    # --- filler language (Part 3, Tier 1) ---
    filler = quality_rules.find_filler(text)
    if not filler:
        points += budget["filler"]
    else:
        findings.append(Finding(
            id="summary.filler_language", category="summary",
            target_ref="summary.text",
            severity=_sev(budget["filler"]), points=budget["filler"],
            message=f"Filler phrase: \u201c{filler[0]}\u201d",
            fix_hint="Every candidate claims this and none of them prove it. "
                     "Replace it with the thing you actually did.",
            action="rewrite", meta={"phrases": filler},
        ))

    return min(points, WEIGHTS["summary"]), findings


# ------------------------------------------------------------ experience ---
def _score_experience(data: dict) -> tuple[int, list[Finding]]:
    entries = data.get("experience", []) or []
    findings: list[Finding] = []

    if not entries:
        return 0, [Finding(
            id="experience.missing", category="experience",
            target_ref="experience", severity="high", points=WEIGHTS["experience"],
            message="No work experience listed",
            fix_hint="Add your roles, most recent first.",
        )]

    base = SUB_BUDGETS["experience"]["base"]
    bullets = [b for e in entries for b in e.get("bullets", [])]
    if not bullets:
        return base, [Finding(
            id="experience.no_bullets", category="experience",
            target_ref="experience", severity="high",
            points=WEIGHTS["experience"] - base,
            message="Experience entries have no bullet points",
            fix_hint="Describe what you did and what changed because of it.",
            # NOT "rewrite". target_ref="experience" is a container, not text:
            # resolve() raises InvalidTargetRef on it, so an AI action here
            # would 422 every single time. There is also nothing to rewrite --
            # no bullet exists yet, and drafting one from a job title alone is
            # ungrounded invention, which is exactly what the critic rejects.
            action="",
        )]

    budget = SUB_BUDGETS["experience"]
    points = base

    # --- quantification ---
    metric_max = budget["metric"]
    with_metrics = sum(1 for b in bullets if _METRIC_RE.search(b))
    # FLOOR, not round. With round() the award depended on the size of the
    # budget: at 12 points one weak bullet in 19 lost a point, but when Part
    # 3 re-sliced the budget to 9, round(18/19 * 9) == 9 handed out FULL
    # marks and the finding vanished -- a detection threshold created by
    # accident. Flooring means the full budget is earned only when nothing
    # is wrong, whatever the budget happens to be.
    metric_award = int((with_metrics / len(bullets)) * metric_max)
    points += metric_award
    if metric_award < metric_max:
        # Point at the FIRST unquantified bullet so "Fix" has somewhere to go.
        ref = next(
            (r for r, _p, t in _iter_experience_bullets(data)
             if not _METRIC_RE.search(t)),
            "experience",
        )
        findings.append(Finding(
            id="experience.unquantified_bullets", category="experience",
            target_ref=ref, severity=_sev(metric_max - metric_award),
            points=metric_max - metric_award,
            message=f"Only {with_metrics} of {len(bullets)} bullets are quantified",
            fix_hint="Add the number you moved: %, time saved, revenue, scale.",
            # Same mechanism as summary.no_metric above: restructure for a
            # metric, never supply one.
            action="quantify",
            meta={"quantified": with_metrics, "total": len(bullets)},
        ))

    # --- action verbs ---
    verb_max = budget["action_verb"]
    strong = sum(
        1 for b in bullets
        if b.strip().split()[:1] and b.strip().split()[0].lower() in BULLET_OPENER_VERBS
    )
    verb_award = int((strong / len(bullets)) * verb_max)  # floor: see above
    points += verb_award
    if verb_award < verb_max:
        ref = next(
            (r for r, _p, t in _iter_experience_bullets(data)
             if not (t.strip().split()[:1]
                     and t.strip().split()[0].lower() in BULLET_OPENER_VERBS)),
            "experience",
        )
        findings.append(Finding(
            id="experience.weak_verbs", category="experience", target_ref=ref,
            severity=_sev(verb_max - verb_award), points=verb_max - verb_award,
            message=f"{len(bullets) - strong} of {len(bullets)} bullets "
                    f"do not start with an action verb",
            fix_hint="Start with what you DID: Led, Built, Reduced, Migrated.",
            action="rewrite",
            meta={"strong": strong, "total": len(bullets)},
        ))

    # --- dates (worth 5) ---
    # date_label() covers structured start/end, a preserved ambiguous string,
    # and un-migrated legacy data alike — so the check does not punish a
    # resume merely for not having been migrated yet.
    missing_dates = [i for i, e in enumerate(entries) if not date_label(e)]
    if not missing_dates:
        points += budget["dates"]
    else:
        findings.append(Finding(
            id="experience.missing_dates", category="experience",
            target_ref=f"exp_{missing_dates[0]}",
            severity=_sev(budget["dates"]), points=budget["dates"],
            message=f"{len(missing_dates)} role(s) missing dates",
            fix_hint="Employment gaps are judged less harshly than missing dates.",
            meta={"entries": missing_dates},
        ))

    points += _score_bullet_quality(data, budget, findings)

    return min(points, WEIGHTS["experience"]), findings


def _score_bullet_quality(
    data: dict, budget: dict[str, int], findings: list[Finding]
) -> int:
    """Part 3 Tier 1: five deterministic writing-quality checks over bullets.

    Each is all-or-nothing rather than proportional. A proportional award
    would make the card read "worth 2 points" while fixing one of three
    offending bullets moved the score by less than that, breaking the
    `Δscore == points` invariant the whole scorer is built around.

    Every check points `target_ref` at the FIRST offending bullet so the
    card's action has somewhere to go, and lists the rest in `meta` so the
    UI can say how many remain.
    """
    points = 0
    refs = list(_iter_experience_bullets(data))

    # --- filler language ---
    filler_hits = [
        (ref, quality_rules.find_filler(text)) for ref, _p, text in refs
    ]
    filler_hits = [(ref, hits) for ref, hits in filler_hits if hits]
    if not filler_hits:
        points += budget["filler"]
    else:
        phrases = sorted({p for _r, hits in filler_hits for p in hits})
        findings.append(Finding(
            id="experience.filler_language", category="experience",
            target_ref=filler_hits[0][0],
            severity=_sev(budget["filler"]), points=budget["filler"],
            message=f"{len(filler_hits)} bullet(s) use filler: "
                    f"\u201c{phrases[0]}\u201d",
            fix_hint="A recruiter has read this phrase a thousand times. "
                     "State the work instead.",
            action="rewrite",
            meta={"refs": [r for r, _h in filler_hits], "phrases": phrases},
        ))

    # --- impossible metrics ---
    impossible = [
        (ref, quality_rules.find_impossible_metrics(text))
        for ref, _p, text in refs
    ]
    impossible = [(ref, hits) for ref, hits in impossible if hits]
    if not impossible:
        points += budget["impossible_metric"]
    else:
        findings.append(Finding(
            id="experience.impossible_metric", category="experience",
            target_ref=impossible[0][0],
            severity=_sev(budget["impossible_metric"]),
            points=budget["impossible_metric"],
            message=f"Figure that cannot be right: {impossible[0][1][0]}",
            fix_hint="A 100% reduction means the number reached zero. If you "
                     "meant a large improvement, give the before and after.",
            # `quantify`, not `rewrite`: the correct number is a fact only
            # the candidate has, so this routes into the existing [__]
            # placeholder path rather than letting the writer invent one.
            action="quantify",
            meta={"refs": [r for r, _h in impossible],
                  "values": [h for _r, hits in impossible for h in hits]},
        ))

    # --- first person ---
    first_person = [
        ref for ref, _p, text in refs if quality_rules.find_first_person(text)
    ]
    if not first_person:
        points += budget["first_person"]
    else:
        findings.append(Finding(
            id="experience.first_person", category="experience",
            target_ref=first_person[0],
            severity=_sev(budget["first_person"]),
            points=budget["first_person"],
            message=f"{len(first_person)} bullet(s) use \u201cI\u201d, "
                    f"\u201cwe\u201d or \u201cmy\u201d",
            fix_hint="Resume bullets are already written as you. Drop the "
                     "pronoun and open with the verb.",
            action="rewrite", meta={"refs": first_person},
        ))

    # --- repeated opener, scoped to ONE role ---
    repeated: list[tuple[str, str, int]] = []
    for i, entry in enumerate(data.get("experience", []) or []):
        entry_bullets = entry.get("bullets", []) or []
        for word, idxs in quality_rules.find_repeated_openers(entry_bullets).items():
            repeated.append((f"exp_{i}.bullet_{idxs[0]}", word, len(idxs)))
    if not repeated:
        points += budget["repeated_opener"]
    else:
        ref, word, count = repeated[0]
        findings.append(Finding(
            id="experience.repeated_opener", category="experience",
            target_ref=ref,
            severity=_sev(budget["repeated_opener"]),
            points=budget["repeated_opener"],
            message=f"{count} bullets in one role open with "
                    f"\u201c{word.title()}\u201d",
            fix_hint="Vary the opening verb — repetition reads as one job "
                     "described three times.",
            action="rewrite",
            meta={"refs": [r for r, _w, _c in repeated],
                  "openers": sorted({w for _r, w, _c in repeated})},
        ))

    # --- tense mismatch ---
    mismatched: list[str] = []
    for i, entry in enumerate(data.get("experience", []) or []):
        if not quality_rules.role_has_ended(entry, date_label(entry)):
            continue
        entry_bullets = entry.get("bullets", []) or []
        mismatched += [
            f"exp_{i}.bullet_{j}"
            for j in quality_rules.find_present_tense(entry_bullets)
        ]
    if not mismatched:
        points += budget["tense_mismatch"]
    else:
        findings.append(Finding(
            id="experience.tense_mismatch", category="experience",
            target_ref=mismatched[0],
            severity=_sev(budget["tense_mismatch"]),
            points=budget["tense_mismatch"],
            message=f"{len(mismatched)} bullet(s) in a finished role are "
                    f"written in the present tense",
            fix_hint="A role you have left should read in the past tense: "
                     "\u201cManaged\u201d, not \u201cManage\u201d.",
            action="rewrite", meta={"refs": mismatched},
        ))

    return points


def _iter_experience_bullets(data: dict):
    """(target_ref, placement, text) for experience bullets only."""
    for i, entry in enumerate(data.get("experience", []) or []):
        placement = " · ".join(
            x for x in (entry.get("company"), entry.get("role")) if x
        )
        for j, bullet in enumerate(entry.get("bullets", []) or []):
            yield f"exp_{i}.bullet_{j}", placement, bullet


# ---------------------------------------------------------------- format ---
def _score_format(data: dict) -> tuple[int, list[Finding]]:
    findings: list[Finding] = []
    points = 0

    # The 20-point format budget is now 4 + 3 + 5 + 4 + 4, the last four
    # going to ATS parse-safety. WEIGHTS["format"] is unchanged, so the
    # overall score is still out of 100 and `Δscore == points` still holds.
    if data.get("skills"):
        points += 4
    else:
        findings.append(Finding(
            id="format.no_skills", category="format", target_ref="skills",
            severity=_sev(4), points=4, message="No skills section",
            fix_hint="ATS keyword matching leans heavily on this section.",
        ))

    if data.get("education"):
        points += 3
    else:
        findings.append(Finding(
            id="format.no_education", category="format", target_ref="education",
            severity=_sev(3), points=3, message="No education section",
            fix_hint="Add your highest qualification, even if it is older.",
        ))

    text = to_plain_text(data)
    words = len(text.split())
    if 250 <= words <= 900:
        points += 5
    elif words < 250:
        points += 1
        findings.append(Finding(
            id="format.too_thin", category="format", target_ref="experience",
            severity=_sev(4), points=4,
            message=f"Resume looks thin — {words} words",
            fix_hint="Aim for 250–900 words. Add detail to your recent roles.",
            meta={"words": words},
        ))
    else:
        points += 2
        findings.append(Finding(
            id="format.too_long", category="format", target_ref="experience",
            severity=_sev(3), points=3,
            message=f"Resume may exceed two pages — {words} words",
            fix_hint="Cut older roles back to two or three bullets.",
            meta={"words": words},
        ))

    bullets = [b for e in data.get("experience", []) or [] for b in e.get("bullets", [])]
    if bullets:
        overlong = [
            r for r, _p, t in _iter_experience_bullets(data)
            if len(t.split()) > BULLET_HARD_CAP
        ]
        if overlong:
            points += 2
            findings.append(Finding(
                id="format.overlong_bullets", category="format",
                target_ref=overlong[0], severity=_sev(3), points=3,
                message=f"{len(overlong)} bullet(s) are very long",
                fix_hint=f"Keep bullets under about {BULLET_COACHED_MAX} words so they get read.",
                action="rewrite", meta={"refs": overlong},
            ))
        else:
            points += 4

    points += _score_ats(data, findings)
    return min(points, WEIGHTS["format"]), findings


def _score_ats(data: dict, findings: list[Finding]) -> int:
    """Parse-safety: will an enterprise ATS read this correctly? Worth 4.

    Scored inside `format` rather than as a new top-level category so the
    overall score stays out of 100 and every existing `Δscore == points`
    assertion keeps holding.

    Only CONTENT-level checks live here. Layout problems -- multi-column
    scramble, header-band text, tables -- are properties of the template, not
    the data, so they are fixed in the exporter and guarded by a round-trip
    test instead of being nagged about on every analysis.
    """
    points = 0

    bad_dates = ats.unparseable_dates(data)
    if bad_dates:
        findings.append(Finding(
            id="ats.unparseable_dates", category="format",
            target_ref=bad_dates[0], severity=_sev(2), points=2,
            message=f"{len(bad_dates)} date(s) an ATS may not read",
            fix_hint="Use 'Jan 2024 – Mar 2026'. Workday and Taleo infer the "
                     "pattern from your first date, so one odd entry can "
                     "corrupt the whole timeline.",
            meta={"refs": bad_dates},
        ))
    else:
        points += 2

    glyphs = ats.decorative_glyphs(data)
    if glyphs:
        findings.append(Finding(
            id="ats.decorative_glyphs", category="format",
            target_ref="experience", severity=_sev(1), points=1,
            message=f"Decorative characters: {' '.join(glyphs[:5])}",
            fix_hint="Arrows, stars and emoji tokenise as unknown characters. "
                     "Older parsers can treat them as section breaks and drop "
                     "the bullet. Plain text and standard bullets only.",
            meta={"glyphs": glyphs},
        ))
    else:
        points += 1

    if ats.out_of_order_experience(data):
        findings.append(Finding(
            id="ats.not_reverse_chronological", category="format",
            target_ref="experience", severity=_sev(1), points=1,
            message="Experience is not newest-first",
            fix_hint="Most parsers take your current employer to be the first "
                     "entry. Order roles newest first.",
            # The only finding whose fix is a pure reordering, so it gets the
            # third action branch. Until this was set it told the user to
            # reorder their roles and offered no control that could do it.
            action="rerank",
        ))
    else:
        points += 1

    return points


# ----------------------------------------------------------------- steps ---
STEPS = [
    {
        "id": "contact",
        "title": "Contact & Profile Completeness",
        "description": "Checks for consistency and completeness of your contact "
                       "information and personal details.",
    },
    {
        "id": "summary",
        "title": "Professional Summary",
        "description": "Your opening pitch — the few lines a recruiter actually reads.",
    },
    {
        "id": "experience",
        "title": "Work Experience Impact",
        "description": "Whether your bullets show measurable outcomes, not duties.",
    },
    {
        "id": "format",
        "title": "Structure & ATS Readability",
        "description": "Length, required sections, and whether a parser can read it.",
    },
    {
        # Navigable but NOT scored. Certifications and languages say real things
        # about a candidate, but scoring them would mark a novelist down for not
        # being a sysadmin -- that critiques a career, not a document. So this
        # step carries no weight, emits no findings, and never moves the score.
        "id": "extras",
        "title": "Credentials & Extras",
        "description": "Certifications, languages, awards and any section you "
                       "want to add. Optional \u2014 these do not affect your score.",
        "scored": False,
    },
]


def score_resume(data: dict) -> dict:
    """Overall score, per-category breakdown, and structured findings.

    `notes` (list[str]) is retained alongside `findings` so existing clients and
    stored analyses keep working; it is derived, never authored separately.
    """
    contact, contact_f = _score_contact(data)
    summary, summary_f = _score_summary(data)
    experience, experience_f = _score_experience(data)
    fmt, format_f = _score_format(data)

    by_cat = {
        "contact": (contact, contact_f),
        "summary": (summary, summary_f),
        "experience": (experience, experience_f),
        "format": (fmt, format_f),
    }
    categories = {
        name: {
            "score": score,
            "max": WEIGHTS[name],
            "notes": [f.message for f in findings],
            "findings": [f.to_dict() for f in findings],
        }
        for name, (score, findings) in by_cat.items()
    }

    all_findings = [f for _s, fs in by_cat.values() for f in fs]
    return {
        "overall_score": contact + summary + experience + fmt,
        "category_scores": categories,
        "findings": [f.to_dict() for f in all_findings],
    }


def data_fingerprint(data: dict) -> str:
    """A stable hash of the resume content an analysis was written against.

    The LLM commentary in an AnalysisReport is PROSE ABOUT A SNAPSHOT, while
    the findings beside it are recomputed live on every request. The moment
    the user edits a bullet the two disagree, and the prose is the half that
    looks authoritative -- a paragraph reading "the summary leans on generic
    filler" above zero findings, because the filler was just removed, reads
    as the product being broken.

    Comparing scores cannot detect this: a reword that changes no finding
    leaves the score identical while making the prose wrong. So hash the
    content itself.

    Stored inside `trace`, which is a JSON column, so this needs no migration
    and no DB reset. Reports written before this existed have no fingerprint
    and are reported as unknown rather than guessed at.
    """
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_steps(data: dict, scored: dict | None = None) -> list[dict]:
    """The guided-editor step list: one step per scoring category."""
    scored = scored or score_resume(data)
    cats = scored["category_scores"]
    steps = []
    for index, spec in enumerate(STEPS):
        if not spec.get("scored", True):
            # Zeroed on every axis, so `overall == sum(scores)` and
            # `max_score == 100` hold by construction. `status` is "optional",
            # deliberately NOT "clear": a green tick would claim completion for
            # a step the user has never opened.
            steps.append({
                **spec, "index": index, "score": 0, "max": 0,
                "points_available": 0, "finding_count": 0,
                "findings": [], "status": "optional",
            })
            continue
        cat = cats[spec["id"]]
        findings = cat["findings"]
        available = sum(f["points"] for f in findings)
        steps.append({
            **spec,
            "index": index,
            "score": cat["score"],
            "max": cat["max"],
            # What the user can still gain here. Drives "worth N points" and
            # the ordering hint in the UI.
            "points_available": available,
            "finding_count": len(findings),
            "status": "clear" if not findings else (
                "attention" if available >= 5 else "minor"
            ),
            "findings": findings,
        })
    return steps


def infer_role_tags(data: dict, limit: int = 5) -> list[str]:
    """Cheap, deterministic role inference from skills and bullet text."""
    text = to_plain_text(data)
    catalogue = {
        "Backend Engineer": ["python", "fastapi", "django", "api", "microservice"],
        "Frontend Engineer": ["react", "typescript", "css", "frontend", "ui"],
        "DevOps / SRE": ["kubernetes", "docker", "terraform", "ci/cd", "aws", "sre"],
        "Data Engineer": ["airflow", "spark", "etl", "warehouse", "pipeline"],
        "ML Engineer": ["pytorch", "tensorflow", "model", "machine learning", "llm"],
        "Product Manager": ["roadmap", "stakeholder", "backlog", "discovery"],
    }
    scored = []
    for role, terms in catalogue.items():
        hits = count_hits(text, terms)
        if hits:
            scored.append((hits, role))
    scored.sort(reverse=True)
    return [role for _, role in scored[:limit]]


def extract_keywords(text: str, limit: int = 40) -> list[str]:
    """Keyword extraction for job descriptions — deterministic, no LLM."""
    stop = {
        "the", "and", "for", "with", "you", "our", "are", "will", "have", "this",
        "that", "from", "your", "their", "who", "all", "can", "not", "but", "has",
        "was", "were", "they", "them", "its", "into", "out", "how", "what", "why",
        "job", "role", "work", "team", "years", "experience", "ability", "strong",
        "excellent", "good", "great", "plus", "must", "should", "would", "about",
    }
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#.\-/]{1,}", text.lower())
    counts: dict[str, int] = {}
    for token in tokens:
        token = token.strip(".-/")
        if len(token) < 3 or token in stop:
            continue
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [word for word, _ in ranked[:limit]]


def match_keywords(data: dict, keywords: list[str]) -> dict:
    """Split keywords into matched/gap against the resume text."""
    text = to_plain_text(data)
    # Whole-word/phrase matching, expanded through the skill taxonomy so a JD
    # asking for "k8s" is satisfied by a resume that only writes "Kubernetes".
    # Substring matching used to report 50% match on a resume that mentioned
    # none of the keywords ("Go" inside "going", "R" inside everything).
    matched, gap = [], []
    for k in keywords:
        spellings = canonical_aliases(k)
        (matched if any(contains(text, s) for s in spellings) else gap).append(k)
    percent = round(100.0 * len(matched) / len(keywords), 1) if keywords else 0.0
    return {
        "matched_keywords": matched,
        "gap_keywords": gap,
        "all_keywords": keywords,
        "match_percent": percent,
    }
