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
from app.services.resume_ops import (
    date_label,
    extra_entry_line,
    to_plain_text,
    visible_extras,
)
from app.services.text_match import canonical_aliases, contains, count_hits

# ATS table, owner-fixed 2026-10-25 ("exact table, projects scored"):
#   Work Experience   45   titles, keywords, recent hands-on use
#   Skills            30   keyword baseline matching
#   Projects          15   context & frequency (reverses: was unscored)
#   Education+Summary 10   knockout filters
#
# Contact and Formatting & ATS are NOT scored categories any more: contact is
# a knockout filter and formatting is a pass/fail parser-blocker, so their
# checks keep emitting findings but every finding carries 0 points. An ATS
# either reads the file or it does not -- a partial "70% of format" claim was
# always a customer-facing fiction.
WEIGHTS = {"summary": 10, "experience": 45, "skills": 30, "projects": 15}

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
# a bug. (Daily-doubles shipped the same way: see the 2026-10-25 re-slice
# beside SUB_BUDGETS for complex_sentence/ai_language.)
#
# The scorers read these numbers instead of repeating them, and
# `test_score_budgets.py` asserts each category sums to its weight -- the
# guard Part 3 called for, which did not exist while the budgets were
# literals scattered through the file.
# 2026-10-25 re-slice: two more Tier 1 checks (complex sentences, AI-fingerprint
# language) per owner direction, taking points from inside their categories:
#
#   summary     length 6 -> 5, metric 6 -> 5, action_verb 5 -> 4, filler 3 -> 2,
#               freeing 4 for complex_sentence + ai_language
#   experience  metric 15 -> 13, action_verb 9 -> 8, freeing 3 toward the pair,
#               dates 3 -> 2 freeing the last point
#
# Later same day, the owner-supplied ATS table re-cut the categories
# themselves: summary+education 10, experience 45, skills 30, projects 15,
# contact/format advisory-only (0 points).
SUB_BUDGETS: dict[str, dict[str, int]] = {
    # summary/education = 10. Knocked down for the 2026-10-25 ATS table
    # (was 20 shared with contact-15, format-20 in the old slice).
    "summary": {
        "length": 2, "metric": 2, "action_verb": 2, "education": 1,
        "filler": 1, "complex_sentence": 1, "ai_language": 1,
    },
    # experience = 45 (as before).
    "experience": {
        "base": 6, "metric": 13, "action_verb": 8, "dates": 2,
        "filler": 3, "impossible_metric": 3, "first_person": 2,
        "repeated_opener": 2, "tense_mismatch": 2,
        "complex_sentence": 2, "ai_language": 2,
    },
    # skills = 30 (new scored category: keyword baseline section).
    "skills": {
        "present": 10, "breadth": 10, "grouped": 5, "no_duplicates": 5,
    },
    # projects = 15. 2026-10-25 owner direction: the ATS Project Scoring
    # Matrix (Keyword Density & Hard Skills 35 / Tool-Context Association 25 /
    # Metric & Impact 20 / Job Relevance 20) with the metric arm REMOVED --
    # "don't use quantified check for Projects". No number can be invented for
    # the user's own project, and the JD-less analysis cannot score "job
    # relevance" honestly, so the surviving arms are the three static signals
    # the matrix's own logic text spells out:
    #   Keyword Density & Hard Skills (35)  -> tech_stack 6
    #     exact technology terms named in bullets ("Built with Python, Django
    #     and PostgreSQL", not "various modern coding tools")
    #   Tool-Context Association (25)       -> verb_skill 3 + action_verb 2
    #     the [action verb] + [skill/tool] + [result] sentence shape: opener
    #     verb share, plus a verb with a tool within the next few words
    #   Redundancy Bonus (rule 3)           -> core_competency 2
    #     a skill listed in the Skills section AND proven inside a project is
    #     flagged a "core competency"
    #   base 2: every entry carries at least one bullet.
    "projects": {
        "base": 2, "tech_stack": 6, "action_verb": 2,
        "verb_skill": 3, "core_competency": 2,
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
    "authored", "wrote", "prototyped", "added",
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
    "adding", "introducing", "producing", "authoring", "writing", "prototyping",
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
def _contact_findings(data: dict) -> list[Finding]:
    """The contact check is a KNOCKOUT FILTER (2026-10-25 ruling), not a
    scored category.

    Location, LinkedIn, email and phone are hard screener gates: enterprise
    workflows auto-reject applications they cannot route or reach. But the
    2026-10-25 ATS table gives contact no share of the 100 points -- a full
    contact block no longer props up a weak resume. So every check here
    emits an advisory finding worth ZERO points; severity is set by hand
    (severity can no longer be derived from ``_sev(points)`` at 0).
    """
    contact = data.get("contact", {}) or {}
    findings: list[Finding] = []

    # (key, severity, target_ref, message, hint)
    checks = [
        ("name", "high", "contact.name", "Missing name",
         "Your full name should be the first thing on the resume."),
        ("phone", "high", "contact.phone", "Missing phone number",
         "Recruiters often call before they email. Add a reachable number."),
        ("location", "medium", "contact.location", "Missing location",
         "Add at least city and country — many ATS filters sort on location."),
    ]
    for key, severity, ref, msg, hint in checks:
        if not contact.get(key):
            findings.append(Finding(
                id=f"contact.{key}.missing", category="contact", target_ref=ref,
                severity=severity, points=0, message=msg, fix_hint=hint,
            ))

    email = contact.get("email", "") or ""
    if not (email and _EMAIL_RE.match(email)):
        findings.append(Finding(
            id="contact.email.invalid", category="contact",
            target_ref="contact.email", severity="high", points=0,
            message="Missing or malformed email" if email else "Missing email",
            fix_hint="Use a professional address you actually check.",
        ))

    if not contact.get("links"):
        findings.append(Finding(
            id="contact.links.missing", category="contact",
            target_ref="contact.links", severity="medium", points=0,
            message="No profile links",
            fix_hint="Add LinkedIn, GitHub or a portfolio — it is free credibility.",
        ))

    return findings


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

    # --- complex sentences (summary; bullets get it in bullet quality) ---
    complex_hits = quality_rules.find_complex_sentences(text)
    if not complex_hits:
        points += budget["complex_sentence"]
    else:
        findings.append(Finding(
            id="summary.complex_sentence", category="summary",
            target_ref="summary.text",
            severity=_sev(budget["complex_sentence"]),
            points=budget["complex_sentence"],
            message=f"{len(complex_hits)} long or clause-packed sentence(s): "
                    f"\u201c{complex_hits[0][:60]}\u2026\u201d",
            fix_hint="One idea per sentence. Split it, and let the strongest "
                     "fact stand alone.",
            action="rewrite",
            meta={"sentences": [s[:120] for s in complex_hits]},
        ))

    # --- AI-fingerprint language ---
    ai_hits = quality_rules.find_ai_language(text)
    if not ai_hits:
        points += budget["ai_language"]
    else:
        findings.append(Finding(
            id="summary.ai_language", category="summary",
            target_ref="summary.text",
            severity=_sev(budget["ai_language"]), points=budget["ai_language"],
            message=f"Reads machine-written: \u201c{ai_hits[0]}\u201d",
            fix_hint="Screeners pattern-match these words to generated text. "
                     "Write the plain version of the same fact.",
            action="rewrite", meta={"phrases": ai_hits},
        ))

    # --- education presence (Part 3, Tier 1: Edu+Summary share 10) ---
    # The 2026-10-25 ATS table scores education and the summary as one
    # 10-point category, so the presence check lives here rather than in the
    # (now unscored) format pass. Graduation-readiness, recency and the
    # like are not scored: only that the section exists and parses.
    if data.get("education"):
        points += budget["education"]
    else:
        findings.append(Finding(
            id="summary.no_education", category="summary",
            target_ref="education", severity=_sev(budget["education"]),
            points=budget["education"], message="No education section",
            fix_hint="Add your highest qualification, even if it is older.",
        ))

    return min(points, WEIGHTS["summary"]), findings


# ---------------------------------------------------------------- skills ---
def _score_skills(data: dict) -> tuple[int, list[Finding]]:
    """The 30-point keywords-baseline category (2026-10-25 ATS table).

    Band-graded, not per-item: skill count is a weak signal, so diminishing
    returns are deliberate.
    """
    groups = data.get("skills", []) or []
    budget = SUB_BUDGETS["skills"]

    if not groups:
        return 0, [Finding(
            id="skills.missing", category="skills", target_ref="skills",
            severity="high", points=WEIGHTS["skills"],
            message="No skills section",
            fix_hint="ATS keyword matching leans heavily on this section. "
                     "Group it (Languages, Tools, Cloud) with 8+ real skills.",
        )]

    items = [
        item.strip()
        for group in groups
        for item in (group.get("items", []) or [])
        if item and item.strip()
    ]
    points = budget["present"]
    findings: list[Finding] = []

    # --- breadth: 8+ full, 5+ 60%, 3+ 30%, else 0 ---
    breadth_max = budget["breadth"]
    count = len(items)
    if count >= 8:
        breadth = breadth_max
    elif count >= 5:
        breadth = int(breadth_max * 0.6)
    elif count >= 3:
        breadth = int(breadth_max * 0.3)
    else:
        breadth = 0
    points += breadth
    if breadth < breadth_max:
        findings.append(Finding(
            id="skills.too_few", category="skills", target_ref="skills.0.items",
            severity=_sev(breadth_max - breadth),
            points=breadth_max - breadth,
            message=f"Only {count} skill{'s' if count != 1 else ''} listed "
                    f"— aim for 8+ across groups",
            fix_hint="Every real skill you omit is a keyword the ATS cannot "
                     "match you on.",
        ))

    # --- grouping: every group carries a label ---
    unlabeled = [
        i for i, group in enumerate(groups)
        if not (group.get("label") or "").strip()
    ]
    if unlabeled:
        findings.append(Finding(
            id="skills.no_labels", category="skills",
            target_ref=f"skills.{unlabeled[0]}.items",
            severity=_sev(budget["grouped"]),
            points=budget["grouped"],
            message=f"{len(unlabeled)} skill group(s) have no heading",
            fix_hint="Labelled groups (Languages, Tools, Methodologies) parse "
                     "into keyword buckets better than one flat list.",
        ))
    else:
        points += budget["grouped"]

    # --- duplicates ---
    seen: set[str] = set()
    dup = next(
        (
            item for item in items
            if (item.lower() in seen) or (seen.add(item.lower()) or False)
        ),
        None,
    )
    if dup:
        findings.append(Finding(
            id="skills.duplicate", category="skills", target_ref="skills.0.items",
            severity=_sev(budget["no_duplicates"]), points=budget["no_duplicates"],
            message=f"Repeated skill: \u201c{dup}\u201d",
            fix_hint="Repeats earn nothing with an ATS and cost the space "
                     "another keyword could hold.",
        ))
    else:
        points += budget["no_duplicates"]

    return min(points, WEIGHTS["skills"]), findings


# -------------------------------------------------------------- projects ---
# Canonical technology vocabulary for the projects scorers, implementing the
# owner-supplied ATS Project Scoring Matrix (2026-10-25). Seeded from the
# owner's three reference resumes (Tejas_Taradgaonkar_Resume_java /
# node-python / react_java): their project sections must score full marks,
# which fixes the seed set -- Spring Boot, FastAPI, Node Js/Express Js,
# Next.js, PostgreSQL, MySQL, Spring Data JPA / Hibernate, Leaflet, Bootstrap
# and the HubSpot / OpenAI / Geolocation APIs all have to be recognised.
# Superset of what those resumes name, but deliberately software-shaped:
# the matrix being modelled is a developer-resume matrix. Terms are lowercase;
# matching goes through text_match.contains, so "C++"/".NET"-style tokens
# behave and "React.js" never credits "reactive".
TECH_TERMS: set[str] = {
    # languages
    "javascript", "typescript", "python", "java", "kotlin", "scala", "go",
    "rust", "golang", "c++", "c#", ".net", "php", "ruby", "swift", "dart",
    "objective-c", "r", "matlab", "perl", "elixir", "lua", "bash",
    "powershell", "sql", "pl/sql", "html", "css", "sass",
    # js / py / java / php runtimes & frameworks -- dotted and undotted
    # spellings, because the reference resumes write "Node Js/Express Js"
    # as readily as "Node.js"
    "node", "node.js", "nodejs", "node js", "express", "express.js",
    "expressjs", "express js", "react", "react.js", "reactjs", "react js",
    "next.js", "nextjs", "next js", "nuxt", "vue", "vue.js", "angular",
    "angularjs", "svelte", "remix", "gatsby", "jquery", "redux",
    "tailwind", "tailwind css", "bootstrap", "material ui", "shadcn",
    "django", "flask", "fastapi", "spring", "spring boot", "spring mvc",
    "spring data jpa", "hibernate", "jpa", "jakarta", "quarkus", "micronaut",
    "laravel", "symfony", "rails", "ruby on rails", ".net core", "asp.net",
    "nestjs", "nest.js", "fastify", "koa", "gin", "actix", "phoenix",
    "playwright", "selenium", "cypress", "jest", "vitest", "pytest", "junit",
    "mockito",
    # data stores / messaging
    "postgresql", "postgres", "mysql", "mariadb", "sqlite", "mongodb",
    "dynamodb", "cassandra", "redis", "elasticsearch", "opensearch", "neo4j",
    "firebase", "firestore", "supabase", "snowflake", "bigquery", "redshift",
    "kafka", "rabbitmq", "sqs", "celery", "memcached",
    # cloud / devops
    "aws", "ec2", "s3", "lambda", "ecs", "eks", "rds", "azure", "gcp",
    "google cloud", "heroku", "vercel", "netlify", "digitalocean", "docker",
    "kubernetes", "k8s", "helm", "terraform", "ansible", "jenkins",
    "github actions", "gitlab ci", "ci/cd", "nginx", "apache", "linux",
    "grafana", "prometheus", "opentelemetry", "datadog", "sentry", "loki",
    # ai / data (Outreach AI in the reference resumes is a GenAI build)
    "ai", "openai", "openai api", "llm", "genai", "langchain", "llamaindex",
    "hugging face", "rag", "pinecone", "weaviate", "pgvector", "chromadb",
    "embeddings", "tensorflow", "pytorch", "keras", "scikit-learn",
    "pandas", "numpy", "spark", "pyspark", "airflow", "dbt", "tableau",
    "power bi", "machine learning", "deep learning", "nlp",
    "computer vision",
    # apis / protocols / integrations -- several named in the references
    "rest", "rest api", "restful", "restful api", "restful apis", "graphql",
    "grpc", "websocket", "websockets", "oauth", "oauth2", "jwt",
    "webhooks", "stripe", "razorpay", "twilio", "sendgrid", "nodemailer",
    "hubspot", "hubspot api", "salesforce", "shopify", "wordpress", "slack",
    "discord", "telegram",
    "leaflet", "mapbox", "google maps", "google geolocation api",
    "geolocation", "microservices", "webrtc", "socket.io", "three.js",
    # mobile
    "android", "ios", "flutter", "react native", "expo", "swiftui",
    "jetpack compose",
    # orm / platforms
    "prisma", "typeorm", "sequelize", "mongoose", "sqlalchemy", "alembic",
    "strapi", "sanity", "contentful",
}


def _resume_skill_items(data: dict) -> list[str]:
    """Every item the candidate claims in the skills section."""
    return [
        item.strip()
        for group in data.get("skills", []) or []
        for item in (group.get("items", []) or [])
        if item and item.strip()
    ]


def _project_term_set(data: dict) -> set[str]:
    """What "names a technology" means HERE: the static ATS vocabulary plus
    the candidate's own skills items. The union matters -- a specialist
    stack the canned list lacks (Salesforce, SAP) is still a technology, and
    it is exactly the overlap rule 3 of the matrix scores."""
    return TECH_TERMS | {item.lower() for item in _resume_skill_items(data)}


# A metadata label line ("Tech Stack: Next.js, FastAPI, ...") is a keyword
# line, not a sentence: it counts toward the Keyword Density arm but must be
# excused from both verb checks, or the matrix's own reference shape (the
# owner resumes write exactly these lines) would always bleed points. Twin
# of parsing._LABEL_LINE_RE -- widen both or neither.
_TECH_LABEL_RE = re.compile(
    r"^\s*(tech\s*stack|stack|technologies|tech|tools?|built\s+with|"
    r"skills\s+used|frameworks?)\s*[:\u2013\u2014-]",
    re.IGNORECASE,
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#.\-]*")

# Connective filler allowed inside a bare stack listing.
_LIST_STOPWORDS = {"and", "or", "with", "vs", "via", "the", "a", "an"}


def _bullet_tech_hits(bullet: str, terms: set[str]) -> list[str]:
    return [t for t in terms if contains(bullet, t)]


def _tech_token_set(terms: set[str]) -> set[str]:
    """Word-level decomposition of every tech term: "spring boot" -> spring,
    boot. Both words then count as tech-covered inside a stack listing."""
    return {
        token.lower()
        for term in terms for token in _TOKEN_RE.findall(term)
    }


def _is_tech_list_line(text: str, terms: set[str], tech_tokens: set[str]) -> bool:
    """A bare stack listing under a project name -- "Next.js, FastAPI,
    Spring Boot, PostgreSQL" -- is the same keyword line as an explicit
    "Tech Stack: …" label, just unlabelled (the owner's node-python reference
    resume writes it exactly this way). Grading it as a sentence under the
    verb arms punishes that shape, so it is excused like the labelled one.

    Three conditions keep an ordinary sentence -- including the matrix's
    fail example "Possess basic knowledge of AWS EC2 from a college
    project." -- from slipping through: no action-verb word anywhere, at
    least two tech terms, and >= 60% of words accounted for by tech tokens.
    """
    tokens = [t.lower() for t in _TOKEN_RE.findall(text)]
    if len(tokens) < 2:
        return False
    if any(t in BULLET_OPENER_VERBS for t in tokens):
        return False
    if len(_bullet_tech_hits(text, terms)) < 2:
        return False
    covered = sum(
        1 for t in tokens if t in tech_tokens or t in _LIST_STOPWORDS
    )
    return covered / len(tokens) >= 0.6


def _has_verb_tech_pair(bullet: str, terms: set[str]) -> bool:
    """Tool-Context Association, matrix row 2: an action verb with a
    technology in its wake -- the [Action Verb] + [Skill/Tool] + [Result]
    sentence shape from the matrix's own logic text. "Deployed machine
    learning models on AWS EC2 to automate..." passes;
    "Possess basic knowledge of AWS EC2" does not (no action verb).
    The tool must start within 10 words after the verb -- 8 failed the
    matrix's own high-score shape ("Shipped one-click proposal / pitch
    generation with auto-filled pricing plus AI insights").
    """
    tokens = _TOKEN_RE.findall(bullet)
    norm = [t.lower() for t in tokens]
    for i, token in enumerate(norm):
        if token not in BULLET_OPENER_VERBS:
            continue
        window = " ".join(tokens[i + 1: i + 11])
        if window and any(contains(window, term) for term in terms):
            return True
    return False

def _iter_project_bullets(data: dict):
    for i, entry in enumerate(data.get("projects", []) or []):
        for j, bullet in enumerate(entry.get("bullets", []) or []):
            yield f"prj_{i}.bullet_{j}", bullet


def _score_projects(data: dict) -> tuple[int, list[Finding]]:
    """The 15-point projects category, scored on the owner-supplied ATS
    Project Scoring Matrix (2026-10-25) -- Keyword Density 35 / Tool-Context
    Association 25 / Metric & Impact 20 / Job Relevance 20 -- with two arms
    dropped by the same directive:

    * Metric & Impact: "don't use quantified check for Projects." A figure
      for a user's own project is no more knowable to a scoring table than
      it is to the writer, and this scorer's job is to reward what the
      candidate DID, not to manufacture evidence.
    * Job Relevance Mapping: needs a job description to score against, and
      the JD-less analysis has none. That mapping lives in & ATS-only
      flows (Tailor), not here.

    What remains are the static signals the matrix's logic text defines,
      1. Keyword Density & Hard Skills (tech_stack): a bullet credits when it
         names a real technology (TECH_TERMS ∪ the candidate's skills list).
      2. Tool-Context Association (verb_skill): a bullet credits when an
         action verb has a tool in its wake -- the [verb]+[tool]+[result]
         sentence shape. The opener-verb share (action_verb) stays as its own
         slice, identical to before.
      3. Redundancy Bonus (core_competency): a skill that sits in BOTH the
         skills list and a project bullet is flagged a "core competency".
         Score-stops at the first proven skill by design -- the bucket is a
         2-point bonus, and grading it harder would re-penalise the skills
         section's own breadth score.

    Reverses the earlier "projects navigable but unscored" ruling: unlike
    certifications/languages, projects feed keyword context, which the
    table weights, so projects are scored.
    """
    entries = data.get("projects", []) or []
    budget = SUB_BUDGETS["projects"]

    if not entries:
        return 0, [Finding(
            # Medium, not high: unlike experience, an honest resume can have
            # no projects, and the bucket only costs 15.
            id="projects.missing", category="projects", target_ref="projects",
            severity="medium", points=WEIGHTS["projects"],
            message="No projects section",
            fix_hint="2–3 concrete projects put context behind your skill "
                     "keywords — exactly what this bucket scores.",
        )]

    points = 0
    findings: list[Finding] = []

    # --- base: every entry carries at least one bullet ---
    base_max = budget["base"]
    with_bullets = sum(
        1 for entry in entries
        if any((b or "").strip() for b in (entry.get("bullets", []) or []))
    )
    base_award = int((with_bullets / len(entries)) * base_max)
    points += base_award
    if base_award < base_max:
        findings.append(Finding(
            id="projects.no_bullets", category="projects",
            target_ref="projects", severity=_sev(base_max - base_award),
            points=base_max - base_award,
            message=f"{len(entries) - with_bullets} project(s) have no bullets",
            fix_hint="A project with no detail is a keyword shell. One line "
                     "saying what it does, and what with, is enough.",
            action="",
        ))

    # Below the project-list level there is nothing to score.
    bullets = [
        (ref, text.strip())
        for ref, text in _iter_project_bullets(data) if text.strip()
    ]
    if bullets:
        n = len(bullets)
        terms = _project_term_set(data)
        # Sentence bullets only for the verb arms: stack lines -- labelled
        # ("Tech Stack: …") or bare ("Next.js, FastAPI, …") -- are keyword
        # lines, and grading them on sentence shape punishes the exact form
        # the owner's reference resumes write.
        tech_tokens = _tech_token_set(terms)
        sentences = [
            (ref, text) for ref, text in bullets
            if not _TECH_LABEL_RE.match(text)
            and not _is_tech_list_line(text, terms, tech_tokens)
        ]
        hits = {ref: _bullet_tech_hits(text, terms) for ref, text in bullets}

        # --- Keyword Density & Hard Skills (share) ---
        tech_max = budget["tech_stack"]
        with_tech = sum(1 for h in hits.values() if h)
        tech_award = int((with_tech / n) * tech_max)
        points += tech_award
        if tech_award < tech_max:
            ref = next((r for r in hits if not hits[r]), "projects")
            findings.append(Finding(
                id="projects.no_tech_stack", category="projects",
                target_ref=ref, severity=_sev(tech_max - tech_award),
                points=tech_max - tech_award,
                message=f"Only {with_tech} of {n} project bullets name "
                        f"a technology",
                fix_hint="Keyword density is the heaviest projects signal. "
                         "Name the exact stack — “Built with Python, Django "
                         "and PostgreSQL”, not “various modern coding tools”. "
                         "A “Tech Stack: …” line under each project counts.",
                # Only the candidate knows their real stack; there is no
                # honest automated rewrite here.
                action="",
                meta={"with_tech": with_tech, "total": n},
            ))

        # If every bullet is a label line there are no sentences to grade:
        # award both verb arms rather than punishing a form we told them to
        # write. (The density arm still does its job on those lines.)
        if sentences:
            # --- action verbs (share; unchanged semantics) ---
            verb_max = budget["action_verb"]
            strong = sum(
                1 for _ref, b in sentences
                if b.split()[:1] and b.split()[0].lower() in BULLET_OPENER_VERBS
            )
            verb_award = int((strong / len(sentences)) * verb_max)
            points += verb_award
            if verb_award < verb_max:
                ref = next(
                    (r for r, t in sentences
                     if not (t.split()[:1]
                             and t.split()[0].lower() in BULLET_OPENER_VERBS)),
                    "projects",
                )
                findings.append(Finding(
                    id="projects.weak_verbs", category="projects", target_ref=ref,
                    severity=_sev(verb_max - verb_award),
                    points=verb_max - verb_award,
                    message=f"{len(sentences) - strong} of {len(sentences)} project "
                            f"bullets do not start with an action verb",
                    fix_hint="Start with what you DID: Built, Shipped, Automated.",
                    action="rewrite",
                    meta={"strong": strong, "total": len(sentences)},
                ))

            # --- Tool-Context Association (share) ---
            pair_max = budget["verb_skill"]
            paired = {
                ref for ref, b in sentences if _has_verb_tech_pair(b, terms)
            }
            pair_award = int((len(paired) / len(sentences)) * pair_max)
            points += pair_award
            if pair_award < pair_max:
                ref = next(
                    (r for r, _b in sentences if r not in paired), "projects"
                )
                findings.append(Finding(
                    id="projects.verb_skill_gap", category="projects",
                    target_ref=ref, severity=_sev(pair_max - pair_award),
                    points=pair_max - pair_award,
                    message=f"{len(sentences) - len(paired)} of {len(sentences)} "
                            f"project bullets pair no action verb with a technology",
                    fix_hint="Parsers read project lines as [action verb] + "
                             "[tool] + [outcome]: “Deployed ML models on AWS EC2 "
                             "to automate retraining”, not “Possess basic "
                             "knowledge of AWS EC2”. Keep the tool within a few "
                             "words of the verb.",
                    action="",
                    meta={"paired": len(paired), "total": len(sentences)},
                ))
        else:
            points += budget["action_verb"] + budget["verb_skill"]

    # --- Redundancy Bonus: a skill proven inside a project ---
    # (outside the bullets guard on purpose: no bullets, no proof either way)
    core_max = budget["core_competency"]
    skills_items = _resume_skill_items(data)
    if not skills_items:
        # Nothing claimed in the skills section -> nothing to prove here, and
        # skills.missing already costs the full 30 over there. Awarding the
        # free bonus keeps Δscore == points true for THAT finding: the
        # projects number must not quietly move when the skills section is
        # deleted (test_findings.py asserts the exact delta).
        points += core_max
    else:
        project_text = "\n".join(text for _r, text in bullets)
        proven = [item for item in skills_items if contains(project_text, item)]
        if proven:
            points += core_max
        else:
            # Show the candidate which keywords to prove, technology-flavoured
            # items first — those are the ones the matrix calls "critical".
            techy = [i for i in skills_items if i.lower() in TECH_TERMS]
            examples = (techy or skills_items)[:3]
            findings.append(Finding(
                id="projects.skills_unproven", category="projects",
                target_ref="projects", severity=_sev(core_max),
                points=core_max,
                message="No skills-section keyword is backed by a project "
                        f"(e.g. {', '.join(examples)})",
                fix_hint="A skill that appears in BOTH your skills list and "
                         "a project is flagged a core competency — the ATS "
                         "redundancy rule. Point one project bullet at each "
                         "tool you want ranked for.",
                action="",
                meta={"proven": 0, "total": len(skills_items)},
            ))

    return min(points, WEIGHTS["projects"]), findings


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

    # --- complex sentences ---
    # Bullets past BULLET_HARD_CAP belong to format.overlong_bullets; flagging
    # them here as well would fine the same words in two categories.
    complex_skips_longer_flag = BULLET_HARD_CAP
    complex_hits = [
        (ref, sents)
        for ref, _p, text in refs
        if len(text.split()) <= complex_skips_longer_flag
        and (sents := quality_rules.find_complex_sentences(text))
    ]
    if not complex_hits:
        points += budget["complex_sentence"]
    else:
        findings.append(Finding(
            id="experience.complex_sentence", category="experience",
            target_ref=complex_hits[0][0],
            severity=_sev(budget["complex_sentence"]),
            points=budget["complex_sentence"],
            message=f"{len(complex_hits)} bullet(s) run long or pack multiple "
                    f"clauses: \u201c{complex_hits[0][1][0][:60]}\u2026\u201d",
            fix_hint="A bullet is scanned, not read. One idea, one line, "
                     "under about 25 words.",
            action="rewrite",
            meta={"refs": [r for r, _s in complex_hits]},
        ))

    # --- AI-fingerprint language ---
    ai_hits = [
        (ref, phrases)
        for ref, _p, text in refs
        if (phrases := quality_rules.find_ai_language(text))
    ]
    if not ai_hits:
        points += budget["ai_language"]
    else:
        phrases = sorted({p for _r, hits in ai_hits for p in hits})
        findings.append(Finding(
            id="experience.ai_language", category="experience",
            target_ref=ai_hits[0][0],
            severity=_sev(budget["ai_language"]), points=budget["ai_language"],
            message=f"{len(ai_hits)} bullet(s) read machine-written: "
                    f"\u201c{phrases[0]}\u201d",
            fix_hint="Screeners pattern-match these words to generated text. "
                     "Keep the fact, drop the tell.",
            action="rewrite",
            meta={"refs": [r for r, _p in ai_hits], "phrases": phrases},
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
def _format_findings(data: dict) -> list[Finding]:
    """The format/parse-safety check, now a PASS/FAIL parser blocker
    (2026-10-25 ruling), not a scored category.

    Tables, graphics, header/footer text and text boxes decide whether a
    parser reads the resume at all — a gate, not a gradient. So the checks
    here emit advisory findings worth ZERO points; severity is set by hand.
    The section-presence checks that used to live here moved to the scored
    30-point skills pass and the scored 10-point summary pass.
    """
    findings: list[Finding] = []

    text = to_plain_text(data)
    words = len(text.split())
    if words < 250:
        findings.append(Finding(
            id="format.too_thin", category="format", target_ref="experience",
            severity="medium", points=0,
            message=f"Resume looks thin — {words} words",
            fix_hint="Aim for 250–900 words. Add detail to your recent roles.",
            meta={"words": words},
        ))
    elif words > 900:
        findings.append(Finding(
            id="format.too_long", category="format", target_ref="experience",
            severity="medium", points=0,
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
            findings.append(Finding(
                id="format.overlong_bullets", category="format",
                target_ref=overlong[0], severity="medium", points=0,
                message=f"{len(overlong)} bullet(s) are very long",
                fix_hint=f"Keep bullets under about {BULLET_COACHED_MAX} words so they get read.",
                action="rewrite", meta={"refs": overlong},
            ))

    _ats_findings(data, findings)
    return findings


def _ats_findings(data: dict, findings: list[Finding]) -> None:
    """Parse-safety: will an enterprise ATS read this correctly?

    Advisory-only under the 2026-10-25 pass/fail format ruling.

    Only CONTENT-level checks live here. Layout problems -- multi-column
    scramble, header-band text, tables -- are properties of the template, not
    the data, so they are fixed in the exporter and guarded by a round-trip
    test instead of being nagged about on every analysis.
    """
    bad_dates = ats.unparseable_dates(data)
    if bad_dates:
        findings.append(Finding(
            id="ats.unparseable_dates", category="format",
            target_ref=bad_dates[0], severity="medium", points=0,
            message=f"{len(bad_dates)} date(s) an ATS may not read",
            fix_hint="Use 'Jan 2024 – Mar 2026'. Workday and Taleo infer the "
                     "pattern from your first date, so one odd entry can "
                     "corrupt the whole timeline.",
            meta={"refs": bad_dates},
        ))

    glyphs = ats.decorative_glyphs(data)
    if glyphs:
        findings.append(Finding(
            id="ats.decorative_glyphs", category="format",
            target_ref="experience", severity="low", points=0,
            message=f"Decorative characters: {' '.join(glyphs[:5])}",
            fix_hint="Arrows, stars and emoji tokenise as unknown characters. "
                     "Older parsers can treat them as section breaks and drop "
                     "the bullet. Plain text and standard bullets only.",
            meta={"glyphs": glyphs},
        ))

    if ats.out_of_order_experience(data):
        findings.append(Finding(
            id="ats.not_reverse_chronological", category="format",
            target_ref="experience", severity="low", points=0,
            message="Experience is not newest-first",
            fix_hint="Most parsers take your current employer to be the first "
                     "entry. Order roles newest first.",
            # The only finding whose fix is a pure reordering, so it gets the
            # third action branch. Until this was set it told the user to
            # reorder their roles and offered no control that could do it.
            action="rerank",
        ))


# ----------------------------------------------------------------- steps ---
STEPS = [
    {
        # Advisory-only (0 points, 2026-10-25): the knockout filter — location,
        # LinkedIn, email and phone are hard screener gates, so a failure here
        # gets flagged loudly but never moves the score.
        "id": "contact",
        "title": "Contact & Profile Completeness",
        "description": "The knockout filter: can a screener locate, email and "
                       "call you at all?",
        "advisory": True,
    },
    {
        "id": "summary",
        "title": "Professional Summary & Education",
        "description": "Your opening pitch, plus the education entry an ATS "
                       "screens on. Worth 10 points.",
    },
    {
        "id": "skills",
        "title": "Skills & Keywords",
        "description": "The keyword baseline: breadth, grouping and no repeats. "
                       "Worth 30 points.",
    },
    {
        "id": "experience",
        "title": "Work Experience Impact",
        "description": "Whether your bullets show measurable outcomes, not duties. "
                       "Worth 45 points.",
    },
    {
        # Scored 15 under the 2026-10-25 table, reversing the earlier
        # "navigable but unscored" ruling: projects feed keyword context,
        # which the table weights explicitly.
        "id": "projects",
        "title": "Projects",
        "description": "Tech-stack keywords, action verb + tool phrasing, and "
                       "skills proven in a project. Worth 15 points.",
    },
    {
        # Advisory-only (0 points): the pass/fail parser blocker — tables,
        # graphics, header/footer text and text boxes gate whether a parser
        # reads the resume at all, so they gate-hard rather than shade the
        # score.
        "id": "format",
        "title": "Structure & ATS Readability",
        "description": "The parser blocker: pass/fail checks on length, dates "
                       "and characters an ATS may choke on.",
        "advisory": True,
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
    contact_f = _contact_findings(data)
    summary, summary_f = _score_summary(data)
    skills, skills_f = _score_skills(data)
    experience, experience_f = _score_experience(data)
    projects, projects_f = _score_projects(data)
    format_f = _format_findings(data)

    # Scored categories first, in table order; the two advisory passes
    # (contact knockout filter, format parser blocker) come last with a hard
    # 0 so their findings still surface beside the score without touching it.
    by_cat = {
        "summary": (summary, summary_f, False),
        "skills": (skills, skills_f, False),
        "experience": (experience, experience_f, False),
        "projects": (projects, projects_f, False),
        "contact": (0, contact_f, True),
        "format": (0, format_f, True),
    }
    categories = {
        name: {
            "score": score,
            "max": 0 if advisory else WEIGHTS[name],
            "advisory": advisory,
            "notes": [f.message for f in findings],
            "findings": [f.to_dict() for f in findings],
        }
        for name, (score, findings, advisory) in by_cat.items()
    }

    all_findings = [f for _s, fs, _a in by_cat.values() for f in fs]
    return {
        "overall_score": summary + skills + experience + projects,
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
        advisory = spec.get("advisory", False)
        if advisory:
            # 0-point gate: status follows findings, not recoverable points --
            # an advisory finding with points=0 would otherwise always read
            # "clear".
            status = (
                "clear" if not findings
                else "attention" if any(f["severity"] == "high" for f in findings)
                else "minor"
            )
        else:
            status = "clear" if not findings else (
                "attention" if available >= 5 else "minor"
            )
        steps.append({
            **spec,
            "index": index,
            "score": cat["score"],
            "max": cat["max"],
            # What the user can still gain here. Drives "worth N points" and
            # the ordering hint in the UI.
            "points_available": available,
            "finding_count": len(findings),
            "status": status,
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


def _section_text_blobs(data: dict) -> dict[str, str]:
    """Per-section text for WEIGHTED keyword scoring.

    Separate from to_plain_text on purpose: the percent needs to know WHICH
    section each keyword landed in, because a "Python" in the most recent
    role is not the same evidence as a "Python" in the education section.
    Ordering follows the ATS priority the buckets model (45/30/15/10).
    """
    exp_parts: list[str] = []
    for entry in data.get("experience", []):
        exp_parts += [entry.get("company", ""), entry.get("role", ""),
                      entry.get("descriptor", "") or ""]
        exp_parts += entry.get("bullets", [])
    skill_parts: list[str] = []
    for group in data.get("skills", []):
        skill_parts.append(group.get("label", ""))
        skill_parts += group.get("items", [])
    proj_parts: list[str] = []
    for entry in data.get("projects", []):
        proj_parts.append(entry.get("name", ""))
        proj_parts += entry.get("bullets", [])
    other_parts: list[str] = [data.get("summary", {}).get("text", "")]
    for entry in data.get("education", []):
        other_parts += [entry.get("school", ""), entry.get("degree", ""),
                        entry.get("field_of_study", ""), entry.get("grade", "")]
    for section in visible_extras(data):
        other_parts.append(section["title"])
        other_parts += [extra_entry_line(e) for e in section["entries"]]
    return {
        "experience": "\n".join(exp_parts),
        "skills": "\n".join(skill_parts),
        "projects": "\n".join(proj_parts),
        "other": "\n".join(other_parts),
    }


#: JD-match weighting (owner-specified, 2026-10-25): the ATS read of a
#: keyword is strongest in Work Experience, then Skills, then Projects,
#: then Summary/Education/extra knockout content. Base rates 45/30/15/10
#: (sums to 100, so a resume with no Projects section caps at 85 -- the
#: bucket is context, not a floor).
JD_SECTION_WEIGHTS: dict[str, float] = {
    "experience": 45.0,
    "skills": 30.0,
    "projects": 15.0,
    "other": 10.0,
}


def match_keywords(data: dict, keywords: list[str]) -> dict:
    """Split keywords into matched/gap against the resume text,
    with the section-weighted match percent.

    A keyword earns the weight of the BEST section it appears in
    (experience > skills > projects > other); the percent is the matched
    weight as a share of the total weight of the section buckets, so it
    stays on the familiar 0-100 scale."""
    text = to_plain_text(data)
    # Whole-word/phrase matching, expanded through the skill taxonomy so a JD
    # asking for "k8s" is satisfied by a resume that only writes "Kubernetes".
    # Substring matching used to report 50% match on a resume that mentioned
    # none of the keywords ("Go" inside "going", "R" inside everything).
    matched, gap = [], []
    blobs = _section_text_blobs(data)
    weight_order = sorted(
        JD_SECTION_WEIGHTS, key=JD_SECTION_WEIGHTS.get, reverse=True
    )
    earned = 0.0
    for k in keywords:
        spellings = canonical_aliases(k)
        (matched if any(contains(text, s) for s in spellings) else gap).append(k)
        for section in weight_order:
            if any(contains(blobs[section], s) for s in spellings):
                earned += JD_SECTION_WEIGHTS[section]
                break
    total_weight = sum(JD_SECTION_WEIGHTS.values())
    percent = (
        # per keyword, not per section -- a resume with 12 keywords must not
        # be punishable for having "only 8" in any one bucket
        round(100.0 * earned / (total_weight * len(keywords)), 1)
        if keywords else 0.0
    )
    return {
        "matched_keywords": matched,
        "gap_keywords": gap,
        "all_keywords": keywords,
        "match_percent": percent,
    }
