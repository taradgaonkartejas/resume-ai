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
        # Optional sections (certifications, languages, awards, publications,
        # references, or anything the user names). One generic shape with
        # preset-supplied labels, so preview/export/editor each gain ONE code
        # path rather than one per section type. Deliberately a list: order is
        # user-controlled and must survive round-tripping.
        "extras": [],
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


# The six presets. `labels` drives BOTH the editor inputs and the renderers:
# a field whose label is "" is not part of that section type and is never shown.
# This is what keeps a generic four-field entry from feeling generic.
EXTRA_PRESETS: dict[str, dict] = {
    "certifications": {
        "title": "Certifications",
        "labels": {"primary": "Certification", "secondary": "Issuer",
                   "date": "Year", "detail": ""},
    },
    "languages": {
        "title": "Languages",
        "labels": {"primary": "Language", "secondary": "Fluency",
                   "date": "", "detail": ""},
    },
    "awards": {
        "title": "Awards & Honors",
        "labels": {"primary": "Award", "secondary": "Awarded by",
                   "date": "Year", "detail": ""},
    },
    "publications": {
        "title": "Publications",
        "labels": {"primary": "Title", "secondary": "Venue / journal",
                   "date": "Year", "detail": "Link"},
    },
    "references": {
        "title": "References",
        "labels": {"primary": "Name", "secondary": "Title & company",
                   "date": "", "detail": "Email / phone"},
    },
    "custom": {
        "title": "Additional Information",
        "labels": {"primary": "Item", "secondary": "Detail",
                   "date": "", "detail": ""},
    },
}

_EXTRA_FIELDS = ("primary", "secondary", "date", "detail")


def empty_extra_entry() -> dict:
    return {"primary": "", "secondary": "", "date": "", "detail": ""}


def normalise_extra(section: dict) -> dict:
    """Upcast one extras section to the current shape.

    Unknown `kind` values fall back to "custom" rather than being dropped: a
    section the user typed is data, and silently discarding it would be the
    worst possible failure mode for a document editor.
    """
    if not isinstance(section, dict):
        return {"kind": "custom", "title": EXTRA_PRESETS["custom"]["title"],
                "entries": []}

    kind = section.get("kind")
    if kind not in EXTRA_PRESETS:
        kind = "custom"

    title = str(section.get("title") or "").strip() or EXTRA_PRESETS[kind]["title"]

    raw = section.get("entries")
    entries = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            # Tolerate a bare list of strings -- that is what a naive importer
            # or a hand-edited JSON file produces.
            entries.append({**empty_extra_entry(), "primary": item.strip()})
        elif isinstance(item, dict):
            entries.append({f: str(item.get(f) or "").strip() for f in _EXTRA_FIELDS})

    return {"kind": kind, "title": title, "entries": entries}


def extra_entry_line(entry: dict) -> str:
    """One extras entry as a single display line.

    The single formatter shared by TXT, DOCX and PDF, so the three exports can
    never drift into three different renderings of the same data.
    """
    head = " \u2014 ".join(
        x for x in (entry.get("primary"), entry.get("secondary")) if x
    )
    date = (entry.get("date") or "").strip()
    detail = (entry.get("detail") or "").strip()
    if date:
        head = f"{head} ({date})" if head else date
    if detail:
        head = f"{head} \u00b7 {detail}" if head else detail
    return head.strip()


def visible_extras(data: dict) -> list[dict]:
    """Extras sections that have at least one non-empty entry.

    An empty section is stored (the user is mid-edit) but never rendered -- a
    stray heading with nothing under it is worse than no heading at all,
    on screen and for an ATS parser.
    """
    out = []
    for section in data.get("extras") or []:
        entries = [e for e in section.get("entries", []) if extra_entry_line(e)]
        if entries:
            out.append({**section, "entries": entries})
    return out


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

    extras = out.get("extras")
    # Non-dict junk is DROPPED rather than upcast into a phantom empty section.
    # A section the user added but has not filled in yet is a dict and survives;
    # a stray string is malformed data and materialising a titled empty block
    # for it would put something on screen the user never created.
    out["extras"] = [
        normalise_extra(x)
        for x in (extras if isinstance(extras, list) else [])
        if isinstance(x, dict)
    ]

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


#: host -> display label for a profile link.
#:
#: A resume that writes "LinkedIn" over a hyperlink is doing the right thing,
#: and the exports should reproduce that rather than dumping a 54-character
#: URL into the contact header. Mirrored in `frontend/src/lib/links.ts`; the
#: two are pinned together by test_profile_links.py.
PROFILE_HOSTS: dict[str, str] = {
    "linkedin.com": "LinkedIn",
    "github.com": "GitHub",
    "gitlab.com": "GitLab",
    "bitbucket.org": "Bitbucket",
    "stackoverflow.com": "Stack Overflow",
    "medium.com": "Medium",
    "dev.to": "DEV",
    "kaggle.com": "Kaggle",
    "leetcode.com": "LeetCode",
    "hackerrank.com": "HackerRank",
    "behance.net": "Behance",
    "dribbble.com": "Dribbble",
    "x.com": "X",
    "twitter.com": "X",
    "youtube.com": "YouTube",
    "orcid.org": "ORCID",
    "scholar.google.com": "Google Scholar",
}


def _host_of(url: str) -> str:
    """Bare lowercase host, no scheme, no `www.`, no port."""
    rest = url.split("://", 1)[-1]
    host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = host.split("@")[-1].split(":")[0].lower()
    return host[4:] if host.startswith("www.") else host


def _known_host(url: str) -> str | None:
    """The allowlist key this URL belongs to, or None.

    Subdomains count -- `gist.github.com` and `in.linkedin.com` are still
    GitHub and LinkedIn -- but a suffix match alone would accept
    `github.com.phishing.ru`, so the match is anchored on a dot boundary.
    """
    host = _host_of(url)
    for key in PROFILE_HOSTS:
        if host == key or host.endswith("." + key):
            return key
    return None


def profile_label(url: str) -> str:
    """"https://www.linkedin.com/in/tejas-taradgaonkar" -> "LinkedIn"."""
    key = _known_host(url)
    if key:
        return PROFILE_HOSTS[key]
    return _host_of(url) or url


def label_links(urls: list[str]) -> list[tuple[str, str]]:
    """Pair each URL with its display label, disambiguating collisions.

    Two GitHub links both labelled "GitHub" would be indistinguishable, so a
    repeated label takes the last meaningful path segment:
    "GitHub (taradgaonkartejas)".
    """
    labels = [profile_label(u) for u in urls]
    out: list[tuple[str, str]] = []
    for url, label in zip(urls, labels, strict=True):
        if labels.count(label) > 1:
            tail = [p for p in url.split("://")[-1].split("/")[1:] if p]
            if tail:
                label = f"{label} ({tail[-1]})"
        out.append((url, label))
    return out


def with_location(name: str, location: str | None) -> str:
    """"Acme Corp" + "Pune" -> "Acme Corp, Pune".

    Suppresses a location that is already inside the name. A school parsed as
    "CDAC, Pune" carrying location "Pune" must not render "CDAC, Pune, Pune",
    and that combination is the common case, not a corner one -- Indian
    institutions are routinely written with their city in the name.
    """
    name = (name or "").strip()
    place = (location or "").strip()
    if not place:
        return name
    if not name:
        return place
    if place.lower() in name.lower():
        return name
    return f"{name}, {place}"


def education_lines(entry: dict) -> tuple[str, str]:
    """(primary, detail) for one education entry, shared by all four surfaces.

    `field_of_study`, `location` and `grade` are in the canonical shape and
    editable in the step editor, but rendered in **no** export -- the editor
    promised fields the output silently dropped. One formatter rather than
    four keeps the preview honest about what the PDF will contain.
    """
    degree = (entry.get("degree") or "").strip()
    field = (entry.get("field_of_study") or "").strip()
    school = (entry.get("school") or "").strip()
    grade = (entry.get("grade") or "").strip()

    primary = degree
    if field and field.lower() not in degree.lower():
        primary = f"{degree}, {field}".strip(" ,") if degree else field
    if not primary:
        # No degree at all: the school becomes the headline so the entry is
        # never a blank line with a date beside it.
        primary, school = school, ""

    detail = with_location(school, entry.get("location"))
    if grade:
        detail = f"{detail} · {grade}" if detail else grade
    return primary, detail


def experience_lines(entry: dict, inline: bool = False) -> tuple[str, str]:
    """(primary, detail) for one experience entry. `location` was also dropped."""
    role = (entry.get("role") or "").strip()
    company = with_location(entry.get("company", ""), entry.get("location"))
    if inline:
        return " - ".join(x for x in (role, company) if x), ""
    if role:
        return role, company
    return company, ""


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

    # Plain text cannot carry a hyperlink, so a label is all that survives.
    # Chosen deliberately over "LinkedIn: https://..." for consistency with
    # the other three surfaces. Worth knowing the cost: this file is the ATS
    # paste target, and a reader of the TXT alone cannot reach the profile.
    link_labels = [label for _url, label in label_links(
        [x for x in contact.get("links", []) if x]
    )]
    if link_labels:
        lines.append(" · ".join(link_labels))

    summary = data.get("summary", {}).get("text", "")
    if summary:
        lines += ["", "SUMMARY", summary]

    if data.get("experience"):
        lines += ["", "EXPERIENCE"]
        for entry in data["experience"]:
            head = " — ".join(
                x
                for x in (
                    with_location(entry.get("company", ""), entry.get("location")),
                    entry.get("role"),
                )
                if x
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
            primary, detail = education_lines(entry)
            lines.append(
                " — ".join(x for x in (detail, primary, date_label(entry)) if x)
            )

    if data.get("skills"):
        lines += ["", "SKILLS"]
        for group in data["skills"]:
            label = group.get("label", "")
            items = ", ".join(group.get("items", []))
            lines.append(f"{label}: {items}" if label else items)

    for section in visible_extras(data):
        lines += ["", section["title"].upper()]
        lines += [extra_entry_line(e) for e in section["entries"]]

    return "\n".join(lines)
