"""Addressing and patching of the resume JSON by ``target_ref``.

Supported forms:
    summary.text
    exp_2.bullet_1
    prj_1.bullet_0
    skills.0.items
"""

import copy
import re

from app.services.exceptions import InvalidTargetRef

_EXP_RE = re.compile(r"^(exp|prj)_(\d+)\.bullet_(\d+)$")
_SKILLS_RE = re.compile(r"^skills\.(\d+)\.items$")

_SECTION_KEYS = {"exp": "experience", "prj": "projects"}

# Entry lists that carry a date range, and therefore get migrated/normalised.
_DATED_SECTIONS = ("experience", "education", "projects")


def empty_resume() -> dict:
    return {
        # `headline` is the professional title under the name ("Talent
        # Acquisition Specialist"). Every real-world resume template shows one;
        # it is optional and renders only when present.
        "contact": {
            "name": "",
            "headline": "",
            "email": "",
            "phone": "",
            "location": "",
            "links": [],
        },
        "summary": {"text": ""},
        "experience": [],
        "projects": [],
        "education": [],
        "skills": [],
    }


def empty_experience() -> dict:
    return {
        "company": "", "role": "", "location": "",
        "start_date": "", "end_date": "", "current": False,
        "dates": "", "bullets": [],
    }


def empty_education() -> dict:
    return {
        "school": "", "degree": "", "field_of_study": "", "location": "",
        "grade": "", "start_date": "", "end_date": "", "current": False,
        "dates": "",
    }


# --------------------------------------------------------------- dates ---
# Experience dates were a single free-text string. Structured start/end/current
# is what makes tenure, gaps, ordering and a "Present" label possible.
#
# Two rules govern the migration:
#   1. Never guess. A string that does not split cleanly is preserved verbatim
#      in `raw_dates` and rendered as typed. A resume that silently reports the
#      wrong employment dates is far worse than one that shows an odd string.
#   2. Never let the rendered date drift from the structured one. `date_label()`
#      is the single accessor every renderer uses, so the displayed value is
#      always computed from the same fields the editor writes.

_PRESENT_WORDS = {
    "present", "current", "currently", "now", "ongoing", "to date", "date",
}

# A separator that is clearly a range: padded by whitespace, or the word "to".
_PADDED_SEP = re.compile(r"\s+(?:–|—|−|-{1,2}|to|until|through)\s+", re.IGNORECASE)
# Fallback for the unpadded numeric form "2020-2024" / "2020–present".
_TIGHT_SEP = re.compile(
    r"^\s*(\d{4})\s*[–—−-]\s*(\d{4}|present|current|now)\s*$", re.IGNORECASE
)


def _is_present(token: str) -> bool:
    return token.strip().strip(".").lower() in _PRESENT_WORDS


def split_dates(raw: str) -> dict | None:
    """Split a free-text date range into {start_date, end_date, current}.

    Returns None when the string cannot be split confidently, so the caller
    can preserve it verbatim rather than inventing an employment history.
    """
    text = (raw or "").strip()
    if not text:
        return {"start_date": "", "end_date": "", "current": False}

    if _is_present(text):
        return {"start_date": "", "end_date": "", "current": True}

    parts = _PADDED_SEP.split(text, maxsplit=1)
    if len(parts) != 2:
        tight = _TIGHT_SEP.match(text)
        parts = [tight.group(1), tight.group(2)] if tight else []

    if len(parts) != 2:
        return None

    start, end = parts[0].strip(), parts[1].strip()
    if not start:
        return None
    if _is_present(end):
        return {"start_date": start, "end_date": "", "current": True}
    if not end:
        return None
    return {"start_date": start, "end_date": end, "current": False}


def date_label(entry: dict) -> str:
    """The display string for one entry's date range.

    THE single accessor for rendering. Exporters, the preview and plain-text
    flattening all call this, so a template can never show a value that
    disagrees with the structured fields the editor wrote.
    """
    if not isinstance(entry, dict):
        return ""
    # An unsplittable legacy string is shown exactly as the user wrote it.
    raw = (entry.get("raw_dates") or "").strip()
    if raw:
        return raw

    start = (entry.get("start_date") or "").strip()
    end = (entry.get("end_date") or "").strip()
    if entry.get("current"):
        return f"{start} – Present" if start else "Present"
    if start and end:
        return f"{start} – {end}"
    if start or end:
        return start or end
    # Nothing structured: fall back to the legacy field for un-migrated data.
    return (entry.get("dates") or "").strip()


def normalise_entry(entry: dict) -> dict:
    """Upcast one entry in place-ish and refresh its derived `dates` mirror."""
    if not isinstance(entry, dict):
        return entry
    out = dict(entry)

    has_structured = any(
        out.get(k) for k in ("start_date", "end_date")
    ) or out.get("current") is True

    if not has_structured and not out.get("raw_dates"):
        legacy = (out.get("dates") or "").strip()
        if legacy:
            split = split_dates(legacy)
            if split is None:
                # Ambiguous — keep it verbatim instead of guessing.
                out["raw_dates"] = legacy
                out.setdefault("start_date", "")
                out.setdefault("end_date", "")
                out.setdefault("current", False)
            else:
                out.update(split)
                out.pop("raw_dates", None)
        else:
            out.setdefault("start_date", "")
            out.setdefault("end_date", "")
            out.setdefault("current", False)

    out.setdefault("start_date", "")
    out.setdefault("end_date", "")
    out.setdefault("current", bool(out.get("current")))
    # `dates` is a derived mirror kept for any consumer that still reads it.
    # It is never an input once structured fields exist.
    out["dates"] = date_label(out)
    return out


def migrate(data: dict) -> dict:
    """Upcast a resume document to the current shape.

    Idempotent: running it twice is identical to running it once, which
    matters because it runs on both read and write.
    """
    if not isinstance(data, dict):
        return empty_resume()

    out = copy.deepcopy(data)
    base = empty_resume()
    for key, default in base.items():
        out.setdefault(key, default)

    contact = out.get("contact") or {}
    for key, default in base["contact"].items():
        contact.setdefault(key, default)
    out["contact"] = contact

    if not isinstance(out.get("summary"), dict):
        out["summary"] = {"text": str(out.get("summary") or "")}
    out["summary"].setdefault("text", "")

    for section in _DATED_SECTIONS:
        entries = out.get(section) or []
        if isinstance(entries, list):
            out[section] = [normalise_entry(e) for e in entries]

    return out


def resolve(data: dict, target_ref: str) -> str:
    """Return the current text at ``target_ref``.

    Raises InvalidTargetRef if the path does not exist.
    """
    if target_ref == "summary.text":
        return str(data.get("summary", {}).get("text", ""))

    match = _EXP_RE.match(target_ref)
    if match:
        prefix, idx, bullet_idx = match.group(1), int(match.group(2)), int(match.group(3))
        section = data.get(_SECTION_KEYS[prefix], [])
        if idx >= len(section):
            raise InvalidTargetRef(f"{target_ref}: no such entry")
        bullets = section[idx].get("bullets", [])
        if bullet_idx >= len(bullets):
            raise InvalidTargetRef(f"{target_ref}: no such bullet")
        return str(bullets[bullet_idx])

    match = _SKILLS_RE.match(target_ref)
    if match:
        idx = int(match.group(1))
        groups = data.get("skills", [])
        if idx >= len(groups):
            raise InvalidTargetRef(f"{target_ref}: no such skill group")
        return ", ".join(groups[idx].get("items", []))

    raise InvalidTargetRef(f"Unrecognised target_ref: {target_ref}")


def exists(data: dict, target_ref: str) -> bool:
    try:
        resolve(data, target_ref)
        return True
    except InvalidTargetRef:
        return False


def apply_patch(data: dict, target_ref: str, new_text: str) -> dict:
    """Return a deep-ish copy of ``data`` with ``target_ref`` replaced."""
    import copy

    patched = copy.deepcopy(data)

    if target_ref == "summary.text":
        patched.setdefault("summary", {})["text"] = new_text
        return patched

    match = _EXP_RE.match(target_ref)
    if match:
        prefix, idx, bullet_idx = match.group(1), int(match.group(2)), int(match.group(3))
        section = patched.get(_SECTION_KEYS[prefix], [])
        if idx >= len(section):
            raise InvalidTargetRef(f"{target_ref}: no such entry")
        bullets = section[idx].setdefault("bullets", [])
        if bullet_idx >= len(bullets):
            raise InvalidTargetRef(f"{target_ref}: no such bullet")
        bullets[bullet_idx] = new_text
        return patched

    match = _SKILLS_RE.match(target_ref)
    if match:
        idx = int(match.group(1))
        groups = patched.get("skills", [])
        if idx >= len(groups):
            raise InvalidTargetRef(f"{target_ref}: no such skill group")
        groups[idx]["items"] = [s.strip() for s in new_text.split(",") if s.strip()]
        return patched

    raise InvalidTargetRef(f"Unrecognised target_ref: {target_ref}")


def iter_bullets(data: dict):
    """Yield (target_ref, placement, text) for every addressable bullet."""
    summary = data.get("summary", {}).get("text", "")
    if summary:
        yield "summary.text", "Summary", summary

    for prefix, key in _SECTION_KEYS.items():
        for i, entry in enumerate(data.get(key, [])):
            company = entry.get("company") or entry.get("name", "")
            role = entry.get("role", "")
            placement = " — ".join(x for x in (company, role) if x)
            for j, bullet in enumerate(entry.get("bullets", [])):
                yield f"{prefix}_{i}.bullet_{j}", placement, bullet


def to_plain_text(data: dict) -> str:
    """Flatten the resume for TXT export and keyword matching."""
    lines: list[str] = []
    contact = data.get("contact", {})
    if contact.get("name"):
        lines.append(contact["name"])
    detail = " | ".join(
        x for x in (contact.get("email"), contact.get("phone"), contact.get("location")) if x
    )
    if detail:
        lines.append(detail)

    summary = data.get("summary", {}).get("text", "")
    if summary:
        lines += ["", "SUMMARY", summary]

    if data.get("experience"):
        lines += ["", "EXPERIENCE"]
        for entry in data["experience"]:
            head = " — ".join(
                x for x in (entry.get("company"), entry.get("role")) if x
            )
            dates = date_label(entry)
            lines.append(f"{head} {dates}".strip())
            lines += [f"  - {b}" for b in entry.get("bullets", [])]

    if data.get("projects"):
        lines += ["", "PROJECTS"]
        for entry in data["projects"]:
            lines.append(entry.get("name", ""))
            lines += [f"  - {b}" for b in entry.get("bullets", [])]

    if data.get("education"):
        lines += ["", "EDUCATION"]
        for entry in data["education"]:
            lines.append(
                " — ".join(
                    x for x in (entry.get("school"), entry.get("degree"),
                                date_label(entry)) if x
                )
            )

    if data.get("skills"):
        lines += ["", "SKILLS"]
        for group in data["skills"]:
            label = group.get("label", "")
            items = ", ".join(group.get("items", []))
            lines.append(f"{label}: {items}" if label else items)

    return "\n".join(lines)
