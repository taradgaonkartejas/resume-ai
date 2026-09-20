"""Addressing and patching of the resume JSON by ``target_ref``.

Supported forms:
    summary.text
    exp_2.bullet_1
    prj_1.bullet_0
    skills.0.items
"""

import re

from app.services.exceptions import InvalidTargetRef

_EXP_RE = re.compile(r"^(exp|prj)_(\d+)\.bullet_(\d+)$")
_SKILLS_RE = re.compile(r"^skills\.(\d+)\.items$")

_SECTION_KEYS = {"exp": "experience", "prj": "projects"}


def empty_resume() -> dict:
    return {
        "contact": {"name": "", "email": "", "phone": "", "location": "", "links": []},
        "summary": {"text": ""},
        "experience": [],
        "projects": [],
        "education": [],
        "skills": [],
    }


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
            dates = entry.get("dates", "")
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
                    x for x in (entry.get("school"), entry.get("degree"), entry.get("dates")) if x
                )
            )

    if data.get("skills"):
        lines += ["", "SKILLS"]
        for group in data["skills"]:
            label = group.get("label", "")
            items = ", ".join(group.get("items", []))
            lines.append(f"{label}: {items}" if label else items)

    return "\n".join(lines)
