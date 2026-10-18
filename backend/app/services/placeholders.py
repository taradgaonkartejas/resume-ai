"""The `[__]` metric placeholder.

Two findings -- `summary.no_metric` and `experience.unquantified_bullets` --
are cleared only by a number, and the writer is forbidden from inventing one.
That prohibition is the single most important safety property in the product:
a fabricated "cut latency by 40%" on a resume in front of a recruiter is the
worst thing this tool could do.

So the writer does not supply the figure. It **restructures the line so the
figure fits** and leaves a visible blank the candidate fills in:

    before: Improved processing performance for large point-cloud datasets
    after:  Improved processing performance for large point-cloud datasets
            by [__]%

Zero fabrication risk, because nothing is asserted. The placeholder is also
why this module exists rather than a literal scattered across the writer, the
guards and the exporter: a sentinel that three files spell independently is a
sentinel that eventually disagrees with itself.
"""

import re

#: The literal the writer emits and the candidate replaces.
#:
#: Deliberately bracketed and symmetric: it must be impossible to mistake for
#: real resume text, must survive a round trip through PDF/DOCX extraction,
#: and must be greppable. Underscores rather than "TODO" or "XX" because those
#: appear in genuine resumes (a repo called TODO, a company called XX Labs).
PLACEHOLDER = "[__]"

#: Findings whose fix is a number only the candidate has.
QUANTIFY_FINDINGS = frozenset(
    {"summary.no_metric", "experience.unquantified_bullets"}
)

_DIGITS_RE = re.compile(r"\d+(?:\.\d+)?")


def count(text: str) -> int:
    """How many placeholders `text` contains."""
    return (text or "").count(PLACEHOLDER)


def has(text: str) -> bool:
    return count(text) > 0


def new_digits(original: str, draft: str) -> list[str]:
    """Numbers in `draft` that were not in `original`.

    Stricter than `unverifiable_numbers`, which compares against the WHOLE
    resume. For a quantify rewrite that is not enough: a number lifted from
    another job's bullet into this one is still a fabricated claim about this
    role, even though it does appear somewhere in the document.

    Multiset-aware, so repeating an existing figure a second time counts as
    new. "40 services" becoming "40 services across 40 regions" invents the
    second 40.
    """
    before = _DIGITS_RE.findall(original or "")
    after = _DIGITS_RE.findall(draft or "")
    remaining = list(before)
    invented: list[str] = []
    for n in after:
        if n in remaining:
            remaining.remove(n)
        else:
            invented.append(n)
    return invented


def check_draft(original: str, draft: str) -> str | None:
    """Why this quantify draft is unacceptable, phrased for the writer.

    Returns None when the draft is fine. The two rules are the whole point of
    the mechanism, so they are enforced rather than merely requested:

    1. exactly one placeholder -- none means the writer silently reverted to
       an ordinary rewrite and the card would claim to have made room for a
       metric it did not make room for; several means the candidate is handed
       a puzzle instead of a blank.
    2. no new digits -- the prohibition the whole design exists to honour.
    """
    n = count(draft)
    if n == 0:
        return (
            f"it contains no {PLACEHOLDER} placeholder. Restructure the line "
            f"so a figure fits and put {PLACEHOLDER} exactly where the "
            "candidate's number belongs"
        )
    if n > 1:
        return (
            f"it contains {n} {PLACEHOLDER} placeholders. Use exactly one, so "
            "there is a single blank to fill in"
        )
    invented = new_digits(original, draft)
    if invented:
        return (
            "it states "
            + ", ".join(invented)
            + f", which the original line does not contain. The {PLACEHOLDER} "
            "is where the number goes -- do not supply one yourself"
        )
    return None


def find_in_resume(structured_data: dict) -> list[str]:
    """Every target ref whose text still holds an unfilled placeholder.

    Used by the export guard. Shipping a resume containing a literal "[__]"
    to a recruiter is a worse outcome than the missing metric the placeholder
    was introduced to fix, so export refuses rather than warns.
    """
    from app.services import resume_ops

    hits: list[str] = []
    data = structured_data or {}

    summary = (data.get("summary") or {}).get("text") or ""
    if has(summary):
        hits.append("summary.text")

    for ref, _placement, text in resume_ops.iter_bullets(data):
        if has(text):
            hits.append(ref)

    for i, group in enumerate(data.get("skills") or []):
        if any(has(item) for item in (group.get("items") or [])):
            hits.append(f"skills.{i}.items")

    return hits
