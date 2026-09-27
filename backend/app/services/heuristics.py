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

import re
from dataclasses import asdict, dataclass, field

from app.services.resume_ops import date_label, to_plain_text

WEIGHTS = {"contact": 15, "summary": 20, "experience": 45, "format": 20}

ACTION_VERBS = {
    "led", "built", "designed", "migrated", "reduced", "improved", "launched",
    "automated", "scaled", "delivered", "implemented", "architected", "optimized",
    "optimised", "created", "drove", "shipped", "owned", "established", "mentored",
}

_METRIC_RE = re.compile(r"(\d+(\.\d+)?\s*%|\$\s?\d|\b\d{2,}\b|\bx\d+\b)")
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
    action: str = ""  # "" | "rewrite" | "rerank"
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

    words = len(text.split())
    length_points = 8 if words >= 25 else (5 if words >= 12 else 2)
    points = length_points
    if length_points < 8:
        findings.append(Finding(
            id="summary.too_short", category="summary", target_ref="summary.text",
            severity=_sev(8 - length_points), points=8 - length_points,
            message=f"Summary is short ({words} words) — aim for 30–60",
            fix_hint="Name the role you want and quantify one achievement.",
            action="rewrite", meta={"words": words},
        ))

    if _METRIC_RE.search(text):
        points += 6
    else:
        findings.append(Finding(
            id="summary.no_metric", category="summary", target_ref="summary.text",
            severity=_sev(6), points=6,
            message="Summary has no quantified achievement",
            fix_hint="Add a real number — team size, users served, latency cut.",
            action="rewrite",
        ))

    if any(v in text.lower() for v in ACTION_VERBS):
        points += 6
    else:
        findings.append(Finding(
            id="summary.no_action_verb", category="summary",
            target_ref="summary.text", severity=_sev(6), points=6,
            message="Summary lacks a strong action verb",
            fix_hint="Open with a verb like 'Led', 'Built' or 'Scaled'.",
            action="rewrite",
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

    bullets = [b for e in entries for b in e.get("bullets", [])]
    if not bullets:
        return 6, [Finding(
            id="experience.no_bullets", category="experience",
            target_ref="experience", severity="high",
            points=WEIGHTS["experience"] - 6,
            message="Experience entries have no bullet points",
            fix_hint="Describe what you did and what changed because of it.",
            action="rewrite",
        )]

    points = 10

    # --- quantification (worth up to 18) ---
    with_metrics = sum(1 for b in bullets if _METRIC_RE.search(b))
    metric_award = int(round((with_metrics / len(bullets)) * 18))
    points += metric_award
    if metric_award < 18:
        # Point at the FIRST unquantified bullet so "Fix" has somewhere to go.
        ref = next(
            (r for r, _p, t in _iter_experience_bullets(data)
             if not _METRIC_RE.search(t)),
            "experience",
        )
        findings.append(Finding(
            id="experience.unquantified_bullets", category="experience",
            target_ref=ref, severity=_sev(18 - metric_award),
            points=18 - metric_award,
            message=f"Only {with_metrics} of {len(bullets)} bullets are quantified",
            fix_hint="Add the number you moved: %, time saved, revenue, scale.",
            action="rewrite",
            meta={"quantified": with_metrics, "total": len(bullets)},
        ))

    # --- action verbs (worth up to 12) ---
    strong = sum(
        1 for b in bullets
        if b.strip().split()[:1] and b.strip().split()[0].lower() in ACTION_VERBS
    )
    verb_award = int(round((strong / len(bullets)) * 12))
    points += verb_award
    if verb_award < 12:
        ref = next(
            (r for r, _p, t in _iter_experience_bullets(data)
             if not (t.strip().split()[:1]
                     and t.strip().split()[0].lower() in ACTION_VERBS)),
            "experience",
        )
        findings.append(Finding(
            id="experience.weak_verbs", category="experience", target_ref=ref,
            severity=_sev(12 - verb_award), points=12 - verb_award,
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
        points += 5
    else:
        findings.append(Finding(
            id="experience.missing_dates", category="experience",
            target_ref=f"exp_{missing_dates[0]}", severity=_sev(5), points=5,
            message=f"{len(missing_dates)} role(s) missing dates",
            fix_hint="Employment gaps are judged less harshly than missing dates.",
            meta={"entries": missing_dates},
        ))

    return min(points, WEIGHTS["experience"]), findings


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

    if data.get("skills"):
        points += 5
    else:
        findings.append(Finding(
            id="format.no_skills", category="format", target_ref="skills",
            severity=_sev(5), points=5, message="No skills section",
            fix_hint="ATS keyword matching leans heavily on this section.",
        ))

    if data.get("education"):
        points += 4
    else:
        findings.append(Finding(
            id="format.no_education", category="format", target_ref="education",
            severity=_sev(4), points=4, message="No education section",
            fix_hint="Add your highest qualification, even if it is older.",
        ))

    text = to_plain_text(data)
    words = len(text.split())
    if 250 <= words <= 900:
        points += 6
    elif words < 250:
        points += 2
        findings.append(Finding(
            id="format.too_thin", category="format", target_ref="experience",
            severity=_sev(4), points=4,
            message=f"Resume looks thin — {words} words",
            fix_hint="Aim for 250–900 words. Add detail to your recent roles.",
            meta={"words": words},
        ))
    else:
        points += 3
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
            r for r, _p, t in _iter_experience_bullets(data) if len(t.split()) > 45
        ]
        if overlong:
            points += 2
            findings.append(Finding(
                id="format.overlong_bullets", category="format",
                target_ref=overlong[0], severity=_sev(3), points=3,
                message=f"{len(overlong)} bullet(s) are very long",
                fix_hint="Keep bullets under about 30 words so they get read.",
                action="rewrite", meta={"refs": overlong},
            ))
        else:
            points += 5

    return min(points, WEIGHTS["format"]), findings


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


def build_steps(data: dict, scored: dict | None = None) -> list[dict]:
    """The guided-editor step list: one step per scoring category."""
    scored = scored or score_resume(data)
    cats = scored["category_scores"]
    steps = []
    for index, spec in enumerate(STEPS):
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
    text = to_plain_text(data).lower()
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
        hits = sum(1 for t in terms if t in text)
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
    text = to_plain_text(data).lower()
    matched = [k for k in keywords if k.lower() in text]
    gap = [k for k in keywords if k.lower() not in text]
    percent = round(100.0 * len(matched) / len(keywords), 1) if keywords else 0.0
    return {
        "matched_keywords": matched,
        "gap_keywords": gap,
        "all_keywords": keywords,
        "match_percent": percent,
    }
