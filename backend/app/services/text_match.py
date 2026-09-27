"""Word-boundary term matching.

Everything in this system that asked "does the resume mention X?" used plain
Python `in`, i.e. substring containment. That is wrong for short terms and it
was wrong in four separate places:

    "Go"    matched  going, good, organization, algorithm, logo
    "R"     matched  virtually every resume ever written
    "led"   matched  fulfilled          (action-verb credit for a weak bullet)
    "owned" matched  downed

The damage was not cosmetic: `match_keywords` feeds `match_percent` (the
headline number in a tailoring session) and `gap_keywords` (the *only* input
telling the Writer agent what to add). A gap counted as "matched" is a gap the
candidate never gets help closing.

This module is the single place that answer is computed.

Design notes:

* Boundaries are lookarounds, not `\\b`, because `\\b` is defined against word
  characters and breaks on the terms that matter most here. `re.escape("C++")`
  followed by `\\b` requires a word character after the final `+`, so "C++"
  would never match. We therefore only apply a boundary on a side that actually
  ends in a word character.
* Multi-word terms match across any whitespace run, so "machine  learning" and
  "machine\\nlearning" both match "machine learning".
* Patterns are cached: `match_keywords` is called on every accept in a
  tailoring session, over up to 40 keywords.
"""

from __future__ import annotations

import re
from functools import lru_cache

__all__ = ["contains", "partition", "count_hits", "canonical_aliases"]

_WORDISH = re.compile(r"\w")


@lru_cache(maxsize=2048)
def _pattern(term: str) -> re.Pattern[str] | None:
    """Compile a boundary-aware, case-insensitive pattern for one term."""
    t = term.strip()
    if not t:
        return None

    escaped = re.escape(t)
    # Let any whitespace in the term match any whitespace run in the text.
    escaped = re.sub(r"(?:\\[ ]|\s)+", r"\\s+", escaped)

    # Only guard a side that ends in a word character. "C++" gets a left guard
    # but no right guard; ".NET" gets a right guard but no left guard.
    left = r"(?<!\w)" if _WORDISH.match(t[0]) else ""
    right = r"(?!\w)" if _WORDISH.match(t[-1]) else ""
    return re.compile(left + escaped + right, re.IGNORECASE)


def contains(text: str, term: str) -> bool:
    """True if `term` occurs in `text` as a whole word / phrase."""
    pat = _pattern(term)
    return bool(pat and pat.search(text))


def partition(text: str, terms: list[str]) -> tuple[list[str], list[str]]:
    """Split `terms` into (present, missing), preserving input order."""
    present: list[str] = []
    missing: list[str] = []
    for term in terms:
        (present if contains(text, term) else missing).append(term)
    return present, missing


def count_hits(text: str, terms: list[str]) -> int:
    """How many of `terms` appear. Each term counts at most once."""
    return sum(1 for t in terms if contains(text, t))


def canonical_aliases(term: str, taxonomy: list[dict] | None = None) -> list[str]:
    """Every spelling of `term` we know about, including `term` itself.

    Lets "k8s" in a job description match a resume that only ever writes
    "Kubernetes", which plain matching -- boundary-aware or not -- would miss.
    """
    if taxonomy is None:
        from app.ai.corpora import SKILL_TAXONOMY as taxonomy  # local: avoids a cycle

    lowered = term.strip().lower()
    for entry in taxonomy:
        meta = entry.get("doc_metadata") or {}
        canonical = str(meta.get("canonical", ""))
        aliases = [str(a) for a in (meta.get("aliases") or [])]
        names = [canonical, *aliases]
        if any(n.lower() == lowered for n in names if n):
            # De-duplicate case-insensitively, keep first-seen order.
            out: list[str] = []
            seen: set[str] = set()
            for n in names:
                if n and n.lower() not in seen:
                    seen.add(n.lower())
                    out.append(n)
            return out
    return [term]
