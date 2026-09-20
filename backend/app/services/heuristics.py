"""Deterministic ATS scoring. Code, never an LLM — identical input, identical score.

Weights (DESIGN.md §6.6): Contact 15 / Summary 20 / Experience 45 / Format 20.
"""

import re

from app.services.resume_ops import to_plain_text

WEIGHTS = {"contact": 15, "summary": 20, "experience": 45, "format": 20}

ACTION_VERBS = {
    "led", "built", "designed", "migrated", "reduced", "improved", "launched",
    "automated", "scaled", "delivered", "implemented", "architected", "optimized",
    "optimised", "created", "drove", "shipped", "owned", "established", "mentored",
}

_METRIC_RE = re.compile(r"(\d+(\.\d+)?\s*%|\$\s?\d|\b\d{2,}\b|\bx\d+\b)")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _score_contact(data: dict) -> tuple[int, list[str]]:
    contact = data.get("contact", {})
    notes: list[str] = []
    points = 0
    if contact.get("name"):
        points += 4
    else:
        notes.append("Missing name")
    email = contact.get("email", "")
    if email and _EMAIL_RE.match(email):
        points += 4
    else:
        notes.append("Missing or malformed email")
    if contact.get("phone"):
        points += 3
    else:
        notes.append("Missing phone")
    if contact.get("location"):
        points += 2
    else:
        notes.append("Missing location")
    if contact.get("links"):
        points += 2
    else:
        notes.append("No profile links")
    return min(points, WEIGHTS["contact"]), notes


def _score_summary(data: dict) -> tuple[int, list[str]]:
    text = (data.get("summary", {}).get("text") or "").strip()
    notes: list[str] = []
    if not text:
        return 0, ["No professional summary"]
    words = len(text.split())
    points = 8 if words >= 25 else (5 if words >= 12 else 2)
    if words < 25:
        notes.append("Summary is short — aim for 30-60 words")
    if _METRIC_RE.search(text):
        points += 6
    else:
        notes.append("Summary has no quantified achievement")
    if any(v in text.lower() for v in ACTION_VERBS):
        points += 6
    else:
        notes.append("Summary lacks a strong action verb")
    return min(points, WEIGHTS["summary"]), notes


def _score_experience(data: dict) -> tuple[int, list[str]]:
    entries = data.get("experience", [])
    notes: list[str] = []
    if not entries:
        return 0, ["No work experience listed"]

    bullets = [b for e in entries for b in e.get("bullets", [])]
    if not bullets:
        return 6, ["Experience entries have no bullet points"]

    points = 10
    with_metrics = sum(1 for b in bullets if _METRIC_RE.search(b))
    metric_ratio = with_metrics / len(bullets)
    points += int(round(metric_ratio * 18))
    if metric_ratio < 0.5:
        notes.append(
            f"Only {with_metrics} of {len(bullets)} bullets are quantified"
        )

    strong = sum(
        1 for b in bullets if b.strip().split()[:1] and b.strip().split()[0].lower() in ACTION_VERBS
    )
    verb_ratio = strong / len(bullets)
    points += int(round(verb_ratio * 12))
    if verb_ratio < 0.6:
        notes.append("Several bullets do not start with an action verb")

    if all(e.get("dates") for e in entries):
        points += 5
    else:
        notes.append("Some roles are missing dates")

    return min(points, WEIGHTS["experience"]), notes


def _score_format(data: dict) -> tuple[int, list[str]]:
    notes: list[str] = []
    points = 0
    if data.get("skills"):
        points += 5
    else:
        notes.append("No skills section")
    if data.get("education"):
        points += 4
    else:
        notes.append("No education section")

    text = to_plain_text(data)
    words = len(text.split())
    if 250 <= words <= 900:
        points += 6
    elif words < 250:
        notes.append("Resume looks thin — under 250 words")
        points += 2
    else:
        notes.append("Resume may exceed two pages")
        points += 3

    bullets = [b for e in data.get("experience", []) for b in e.get("bullets", [])]
    if bullets:
        overlong = [b for b in bullets if len(b.split()) > 45]
        if overlong:
            notes.append(f"{len(overlong)} bullet(s) are very long")
            points += 2
        else:
            points += 5
    return min(points, WEIGHTS["format"]), notes


def score_resume(data: dict) -> dict:
    """Return overall score, per-category breakdown and improvement notes."""
    contact, contact_notes = _score_contact(data)
    summary, summary_notes = _score_summary(data)
    experience, experience_notes = _score_experience(data)
    fmt, format_notes = _score_format(data)

    categories = {
        "contact": {"score": contact, "max": WEIGHTS["contact"], "notes": contact_notes},
        "summary": {"score": summary, "max": WEIGHTS["summary"], "notes": summary_notes},
        "experience": {
            "score": experience,
            "max": WEIGHTS["experience"],
            "notes": experience_notes,
        },
        "format": {"score": fmt, "max": WEIGHTS["format"], "notes": format_notes},
    }
    overall = contact + summary + experience + fmt
    return {"overall_score": overall, "category_scores": categories}


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
