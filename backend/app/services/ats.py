"""ATS parse-safety checks.

The existing scorer answers "is this resume any good". This module answers a
different question that fails independently: **will Workday, Taleo, iCIMS or
Greenhouse be able to read the file at all?** A resume can score 90 on content
and still reach a recruiter as a blank profile.

The failure modes below are the ones repeatedly documented across enterprise
parsers, in rough order of how often they cause field-level errors:

    ~34%  multi-column scramble      — the extractor reads across the page
    ~22%  header/footer ghost fields — those layers are skipped entirely
    ~17%  non-standard section labels
    ~15%  date format breakage
    ~12%  decorative glyphs in bullets

Checks split by where the problem lives:

* **Layout** problems (columns, header bands, images, tables) belong to the
  *document*. For a resume exported by us they are a property of our template,
  so they are fixed in `export_service` and guarded by a round-trip test
  rather than nagged about here.
* **Content** problems (dates, glyphs, ordering) travel with the data into any
  template, so those are the ones scored.

Deliberately rule-based. "Does this PDF have two columns" is a document fact,
and an LLM would be slower, costlier and less reliable at measuring it. The
judgement calls that genuinely need a model -- is "My Journey" a Work
Experience heading? -- are handled separately by the ATS agent.
"""

import re

from app.services.resume_ops import split_dates, to_plain_text

#: Characters that tokenise badly in legacy parsers. Arrows, emoji, box
#: drawing, decorative bullets and the symbol-font dingbats that design tools
#: emit. Taleo has been observed treating these as section boundaries, which
#: fragments one bullet into several entries or drops it.
#:
#: Note what is NOT here: "•" (U+2022), "–" and "—". Those are ubiquitous on
#: real resumes, survive every parser, and flagging them would be folklore.
_DECORATIVE = re.compile(
    "["
    "\u2190-\u21ff"  # arrows
    "\u2300-\u23ff"  # technical symbols
    "\u25a0-\u25ff"  # geometric shapes (filled squares, rating circles)
    "\u2600-\u27bf"  # dingbats, checkmarks, stars
    "\u2b00-\u2bff"  # extra arrows and stars
    "\ufe0f"  # variation selector (emoji presentation)
    "\U0001f000-\U0001faff"  # emoji planes
    "]"
)

#: A date a parser can key on: "Jan 2024", "January 2024", "01/2024", "2024".
_PARSEABLE_DATE = re.compile(
    r"^(?:"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}"
    r"|\d{1,2}[/-]\d{4}"
    r"|\d{4}"
    r")$",
    re.IGNORECASE,
)


def decorative_glyphs(data: dict) -> list[str]:
    """Distinct decorative characters anywhere in the resume text."""
    return sorted(set(_DECORATIVE.findall(to_plain_text(data))))


def _entry_dates(data: dict) -> list[tuple[str, dict]]:
    out = []
    for section in ("experience", "education", "projects"):
        for i, entry in enumerate(data.get(section) or []):
            out.append((f"{section}_{i}", entry))
    return out


def unparseable_dates(data: dict) -> list[str]:
    """Refs whose dates a parser cannot key on.

    Workday and Taleo infer a date pattern from the first occurrence and then
    apply it to the rest, so one odd entry can corrupt a whole timeline.
    `split_dates` returning None already means "we could not read this
    confidently" -- the same signal a stricter parser would hit.
    """
    bad: list[str] = []
    for ref, entry in _entry_dates(data):
        start = (entry.get("start_date") or "").strip()
        end = (entry.get("end_date") or "").strip()
        raw = (entry.get("dates") or entry.get("raw_dates") or "").strip()

        if start or end or entry.get("current"):
            for value in (start, end):
                if value and not _PARSEABLE_DATE.match(value):
                    bad.append(ref)
                    break
            continue
        if not raw:
            continue  # absence is a different finding, not a format problem
        if split_dates(raw) is None and not _PARSEABLE_DATE.match(raw):
            bad.append(ref)
    return bad


def _sort_key(entry: dict) -> tuple[int, int]:
    """(year, month) of the end date; "Present" sorts newest."""
    if entry.get("current"):
        return (9999, 12)
    value = (entry.get("end_date") or entry.get("start_date") or "").strip()
    year = re.search(r"(19|20)\d{2}", value)
    if not year:
        return (0, 0)
    month = re.match(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", value, re.IGNORECASE
    )
    months = ["jan", "feb", "mar", "apr", "may", "jun",
              "jul", "aug", "sep", "oct", "nov", "dec"]
    m = months.index(month.group(1).lower()) + 1 if month else 0
    return (int(year.group(0)), m)


def out_of_order_experience(data: dict) -> bool:
    """True when experience is not newest-first.

    Reverse-chronological is what every enterprise parser expects, and several
    compute "most recent employer" as literally the first entry.
    """
    entries = [e for e in (data.get("experience") or []) if e]
    keys = [_sort_key(e) for e in entries]
    dated = [k for k in keys if k != (0, 0)]
    if len(dated) < 2:
        return False
    return dated != sorted(dated, reverse=True)


# ---------------------------------------------------------------------------
# Candidates for the LLM reviewer.
#
# Everything above this line is a finding: deterministic, reproducible, and
# scored. Everything below is a *question* — something that needs world
# knowledge or a judgement call, and so belongs to the agent rather than to
# the rule engine.
#
# The split matters for cost as much as for correctness. These functions are
# the gate: when they return nothing there is no question to ask, and the
# agent is skipped without an LLM call. On a tidy resume that is the common
# case, so the usual Analyze click pays nothing for this feature.
# ---------------------------------------------------------------------------

# A custom section holding dated entries is the signal we care about: it means
# the writer used a heading our classifier -- and therefore a real ATS, which
# matches on the same kind of vocabulary -- did not recognise. Non-standard
# section labels are ~17% of field-level parse errors.
_JOBLIKE_KEYS = ("primary", "secondary", "detail")


def mislabelled_sections(data: dict) -> list[dict]:
    """Custom sections whose *content* looks like a standard resume section.

    "My Journey", "Where I've Been", "Career Highlights" are all real headings
    people use, and all of them hide the work history from a parser that is
    matching on the word "Experience". We can see cheaply that a section is
    custom and that its entries carry dates and employer-shaped text; deciding
    *which* standard section it should be is the agent's call.
    """
    out: list[dict] = []
    for index, section in enumerate(data.get("extras") or []):
        if not isinstance(section, dict) or section.get("kind") != "custom":
            continue
        entries = [e for e in (section.get("entries") or []) if isinstance(e, dict)]
        if not entries:
            continue
        dated = sum(1 for e in entries if str(e.get("date") or "").strip())
        filled = sum(
            1 for e in entries if any(str(e.get(k) or "").strip() for k in _JOBLIKE_KEYS)
        )
        if dated and filled >= len(entries) * 0.5:
            out.append(
                {
                    "ref": f"extras_{index}",
                    "title": str(section.get("title") or "").strip(),
                    "sample": [
                        " — ".join(
                            str(e.get(k) or "").strip()
                            for k in _JOBLIKE_KEYS
                            if str(e.get(k) or "").strip()
                        )
                        for e in entries[:3]
                    ],
                }
            )
    return out


# Workday indexes an acronym and its expansion as different tokens, so a
# recruiter searching "Project Management Professional" does not match a
# resume that only says "PMP". The fix is to carry both -- "Project Management
# Professional (PMP)" -- which is also what the certification bodies print.
#
# Detecting the *candidates* is mechanical. Knowing what AWS, PMP or CKA stand
# for, and whether a given token is even an acronym rather than a product name
# (Go, SQL, R), is not.
_ACRONYM = re.compile(r"^[A-Z][A-Z0-9]{1,5}$")

# Tokens that are the real, full name of the thing. Expanding these would be
# wrong, not merely unnecessary: nobody writes "Structured Query Language".
_NOT_ACRONYMS = frozenset(
    {
        "SQL", "HTML", "CSS", "PHP", "XML", "JSON", "YAML", "HTTP", "HTTPS",
        "REST", "API", "SDK", "CLI", "SSH", "TCP", "UDP", "DNS", "URL", "UI",
        "UX", "IDE", "NPM", "GIT", "JS", "TS", "GO", "C", "R", "SAAS", "ETL",
    }
)


def bare_acronyms(data: dict) -> list[str]:
    """Skills written only as an acronym, which an ATS may not match.

    Returns candidates, not findings. The expansion itself needs world
    knowledge, so the agent supplies it; if the agent is unavailable the user
    simply does not get the tip, which is the right way for this to fail.
    """
    seen: list[str] = []
    for group in data.get("skills") or []:
        if not isinstance(group, dict):
            continue
        for item in group.get("items") or []:
            token = str(item or "").strip()
            if (
                _ACRONYM.match(token)
                and token not in _NOT_ACRONYMS
                and token not in seen
            ):
                seen.append(token)
    return seen
