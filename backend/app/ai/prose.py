"""Recovering structured answers from unstructured model output.

The free UnoRouter models accept `response_format=json_schema` and then ignore
it, replying with markdown prose. The verdict is usually correct -- it is the
envelope that is wrong -- so throwing the response away loses real signal.

Everything here is a *salvage* path. It runs only after the provider has failed
to honour the schema it was asked for, and every recovery is reported with a
distinct status so a run that only worked by salvaging never looks identical to
one where structured output worked.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Words that flip a following "approve" into a refusal. Without this,
# "I cannot approve this proposal" reads as an approval -- the single most
# dangerous misparse available, because it lets a fabrication through.
_NEGATORS = (
    "cannot",
    "can not",
    "can't",
    "cant ",
    "unable",
    "won't",
    "will not",
    "do not",
    "don't",
    "not ",
    "n't",
    "refuse",
    "decline",
    "fail to",
)

_FABRICATION_WORDS = (
    "fabricat",
    "invent",
    "unverified",
    "unsupported",
    "not support",   # matches "not supported" and "does not support"
    "truthful",
    "no evidence",
    "not stated",
    "not present",
    "overstate",
    "inflat",
    "exaggerat",
)

_MARKUP = re.compile(r"[*_#`>]+")


def _flatten(text: str) -> str:
    """Lowercase, strip markdown emphasis, collapse whitespace."""
    return re.sub(r"\s+", " ", _MARKUP.sub("", text or "")).strip().lower()


def read_verdict(text: str) -> bool | None:
    """True = approved, False = rejected, None = could not tell.

    Deliberately conservative. Returning None sends the caller to the rule
    engine, which is the status quo; returning a guess would silently replace
    the anti-fabrication gate with a coin flip.
    """
    head = _flatten(text)[:400]
    if not head:
        return None

    reject_at = head.find("reject")
    approve_at = head.find("approv")

    if reject_at < 0 and approve_at < 0:
        return None
    # Whichever verdict word comes first is the model's actual decision;
    # later mentions are usually the explanation referring back to it.
    if reject_at >= 0 and (approve_at < 0 or reject_at < approve_at):
        return False

    # "approve" leads -- but it may be negated ("I cannot approve ...").
    window = head[max(0, approve_at - 40) : approve_at]
    if any(neg in window for neg in _NEGATORS):
        return False
    return True


def first_sentences(text: str, limit: int = 220) -> str:
    """A short, human-readable reason, with the markdown scaffolding removed."""
    cleaned = _MARKUP.sub("", text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit]
    stop = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[: stop + 1] if stop > 60 else cut).strip() + "\u2026"


def looks_like_fabrication(text: str) -> bool:
    low = (text or "").lower()
    return any(word in low for word in _FABRICATION_WORDS)


# A reply that is a refusal, a question, or commentary ABOUT a rewrite rather
# than the rewrite itself. Accepting one of these as `suggested_text` would
# put "I cannot help with that" into somebody's resume.
_NOT_A_REWRITE = (
    "i cannot", "i can't", "i am unable", "i'm unable", "as an ai",
    "i do not have", "i don't have", "sorry", "unfortunately",
    "could you", "can you provide", "please provide", "what is the",
)

# A PREAMBLE: the model announcing its answer before giving it. Taking the
# whole reply as `suggested_text` pastes "Here is a better bullet:" into the
# resume. Matched only at the start, and only up to a nearby colon, so a
# legitimate "Led migration: cut downtime 40%" is untouched.
_PREAMBLE = re.compile(
    r"^(here|this|that|revised|suggested|improved|rewritten|updated|new|"
    r"better|output|answer|result|draft|version|bullet|summary|rewrite)"
    r"\b[^:]{0,60}:",
    re.I,
)

# META-COMMENTARY: the model explaining itself inside the answer. The
# diffusiongemma decline replies mixed rule citations into the prose, and a
# reader that trusted the whole string would ship them to the user.
_META = re.compile(
    r"\b(violat\w+|rule\s*\d|the original|originally|i (?:have |'ve )?"
    r"(?:changed|rewritten|revised|added|removed)|note that|instead of|"
    r"this version|as requested|per your)\b",
    re.I,
)


def looks_like_a_rewrite(text: str) -> bool:
    """True when `text` could plausibly BE the replacement text.

    Deliberately strict. This backs the prose salvage rung for RevisionDraft,
    whose output is written straight into a resume, so the cost of a false
    positive is high and the cost of a false negative is only that the caller
    degrades exactly as it does today.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    # Paragraph breaks must be read from the ORIGINAL -- _flatten collapses
    # all whitespace, so checking it there silently never matches. (It
    # didn't: the multi-paragraph case passed this guard on the first try.)
    if "\n\n" in raw or "\r\n\r\n" in raw:
        return False
    # Leftover JSON scaffolding means the ok-json rung should have taken it.
    if raw.startswith(("{", "[")) or '":' in raw:
        return False
    t = _flatten(raw)
    if not t:
        return False
    if any(t.startswith(p) or f" {p}" in t[:80] for p in _NOT_A_REWRITE):
        return False
    # Order matters: refusals, preambles and commentary are all detected
    # BEFORE the text is considered usable. A tripwire in
    # test_llm_transport.py required exactly this, and caught the first
    # version of this function accepting "Here is a better bullet: Led X."
    if _PREAMBLE.match(raw.strip()):
        return False
    if _META.search(raw):
        return False
    if t.endswith("?"):
        return False
    # A rewritten bullet or summary is one short block.
    if len(t) > 900 or len(t) < 15:
        return False
    return True


_FENCE = re.compile(r"```(?:json)?\s*(.+?)```", re.S | re.I)


def extract_json(text: str) -> Any | None:
    """Pull the first JSON value out of a response that may be wrapped in prose.

    Handles the common "here is your JSON: ```json {...} ```" shape and bare
    objects embedded in commentary. Returns None when nothing parses -- never
    a partial or repaired object, because a half-read verdict is worse than no
    verdict.
    """
    if not text:
        return None

    candidates: list[str] = []
    fenced = _FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1).strip())
    candidates.append(text.strip())

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            pass
        for opener, closer in (("{", "}"), ("[", "]")):
            block = _balanced(candidate, opener, closer)
            if block is None:
                continue
            try:
                return json.loads(block)
            except (ValueError, TypeError):
                continue
    return None


def _balanced(text: str, opener: str, closer: str) -> str | None:
    """The first balanced opener..closer span, ignoring braces inside strings."""
    start = text.find(opener)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


_NUMBERED = re.compile(
    r"(?im)^\s*(?:\[\s*(\d+)\s*\]|(?:draft|bullet|item|rewrite)\s+(\d+)\b|(\d+)\s*[.)])"
)


def split_numbered(text: str) -> list[tuple[int, str]]:
    """Split a batched prose reply into (index, chunk) pairs.

    Recognises "[2]", "Draft 2", and "2." as section openers. Returns [] when
    the text has no numbering, which the caller treats as "cannot salvage".
    """
    marks = [
        (int(m.group(1) or m.group(2) or m.group(3)), m.start())
        for m in _NUMBERED.finditer(text or "")
    ]
    if not marks:
        return []
    chunks: list[tuple[int, str]] = []
    for pos, (index, start) in enumerate(marks):
        end = marks[pos + 1][1] if pos + 1 < len(marks) else len(text)
        chunks.append((index, text[start:end]))
    return chunks
