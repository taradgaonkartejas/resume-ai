"""Tier 1 deterministic writing-quality rules (PLAN-SUGGESTIONS-DOCX Part 3).

Pure detectors: text in, evidence out. They hold no `Finding` and no points,
so each can be tested on its own and the scorer stays a thin wiring layer.

Two rules shape everything here.

**The line is the unit.** Every detector reads one bullet or one summary and
nothing around it. This is the rule that stopped `clarifying.py` fabricating
"3+ years of GitOps" out of a +/-60-character window: a match that spans a
boundary the writer never wrote is a hallucination with a regex in front of
it.

**A false accusation is worse than silence.** This is the same line Part 3
draws between Tier 1 and Tier 2 — rules are deterministic and scored, model
opinions are advisory and labelled. Where a pattern cannot separate a real
defect from a legitimate sentence, it is narrowed or dropped rather than
left to fire on correct resumes. Each compromise is recorded at its rule.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------- filler ---
#
# Phrases are stored in normalised form: lowercase, every run of non
# alphanumerics collapsed to one space. That is what makes one entry cover
# "results-driven", "results driven" and "Results—Driven" without three
# patterns to keep in step.
FILLER_PHRASES: frozenset[str] = frozenset({
    "proven track record",
    "track record of success",
    "results driven",
    "team player",
    "detail oriented",
    "go getter",
    "self starter",
    "hard working",
    "highly motivated",
    "excellent communication skills",
    "think outside the box",
    "outside the box",
    "dynamic professional",
    "thought leader",
    "win win",
    "synergy",
    "synergies",
})


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def find_filler(text: str) -> list[str]:
    """Filler phrases present in `text`, in the order they are listed.

    Matched on word boundaries against the normalised string, so "synergy"
    does not fire inside a longer word.
    """
    padded = f" {_normalise(text)} "
    return sorted(p for p in FILLER_PHRASES if f" {p} " in padded)


# --------------------------------------------- impossible / implausible ---
#
# Verbs that make a percentage a REDUCTION. "eliminated", "removed" and
# "automated" are deliberately absent: they pair with 100% perfectly sensibly
# ("eliminated 100% of manual steps"), so including them would turn a correct
# sentence into an accusation.
_REDUCTION_VERBS = (
    "reduced", "reducing", "reduce",
    "cut", "cutting",
    "decreased", "decreasing", "decrease",
    "lowered", "lowering", "lower",
    "dropped", "dropping",
    "shrank", "shrunk",
    "slashed", "trimmed",
    "minimised", "minimized",
)
_REDUCTION_RE = re.compile(
    rf"\b(?:{'|'.join(_REDUCTION_VERBS)})\b", re.IGNORECASE
)
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_MULTIPLIER_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s?x\b|\bx\s?(\d+(?:\.\d+)?)\b", re.IGNORECASE
)

#: A reduction of this much or more leaves nothing behind.
REDUCTION_CEILING = 100.0
#: Genuine 100x and even 500x optimisations exist (10 s -> 20 ms). The
#: ceiling is deliberately far above the plausible range so the rule stays
#: silent on real work; only arithmetic nobody can defend trips it.
MULTIPLIER_CEILING = 1000.0

#: A clause break. A bullet may legitimately pair a reduction with a large
#: increase ("cut cost 50% and grew signups 200%"), so the verb is bound to
#: the number inside one clause instead of anywhere on the line.
_CLAUSE_SPLIT_RE = re.compile(r"\band\b|[,;]", re.IGNORECASE)


def find_impossible_metrics(text: str) -> list[str]:
    """Figures that cannot be true as written.

    Two kinds, both objectively checkable:

    * a reduction of >= 100% -- "cut p95 latency by 100%" claims zero
      milliseconds, and anything above 100% claims a negative quantity;
    * a multiplier past `MULTIPLIER_CEILING`.

    An INCREASE above 100% is perfectly ordinary and is never flagged.
    """
    hits: list[str] = []
    for clause in _CLAUSE_SPLIT_RE.split(text):
        if not _REDUCTION_RE.search(clause):
            continue
        for match in _PERCENT_RE.finditer(clause):
            if float(match.group(1)) >= REDUCTION_CEILING:
                hits.append(match.group(0).strip())

    for match in _MULTIPLIER_RE.finditer(text):
        value = match.group(1) or match.group(2)
        if float(value) > MULTIPLIER_CEILING:
            hits.append(match.group(0).strip())

    return hits


# --------------------------------------------------------- first person ---
#
# "I" is the hard one. `\bI\b` also matches the "I" in "I/O" and in
# "Phase I", both of which are normal on an engineering resume, so the
# pronoun is matched case-sensitively and those two shapes are excluded by
# context. "us" and "me" are not in the set at all: "US" (the country) and
# "me" inside a trailing clause produced more noise than they caught.
_FIRST_PERSON_PLAIN_RE = re.compile(r"\b(?:we|we're|my|our|ours|us's)\b", re.IGNORECASE)
_I_RE = re.compile(r"\bI\b")
#: Words that make a following capital "I" a numeral, not a pronoun.
_ORDINAL_CONTEXT = {
    "phase", "type", "tier", "level", "part", "class", "category",
    "section", "appendix", "grade", "version", "group", "stage", "round",
}


def find_first_person(text: str) -> list[str]:
    """First-person pronouns in a bullet, which is written in implied first
    person already -- "I led the team" should read "Led the team"."""
    hits = [m.group(0) for m in _FIRST_PERSON_PLAIN_RE.finditer(text)]

    for match in _I_RE.finditer(text):
        after = text[match.end():match.end() + 2]
        if after.startswith("/"):          # I/O
            continue
        before = text[:match.start()].rstrip()
        prior = re.split(r"[^A-Za-z]+", before)[-1].lower() if before else ""
        if prior in _ORDINAL_CONTEXT:      # Phase I, Type I
            continue
        hits.append("I")

    return hits


# ------------------------------------------------------ repeated opener ---
#: Three identical openers in one role is the point at which a reader starts
#: skimming. Two is a coincidence.
REPEATED_OPENER_THRESHOLD = 3


def opener(bullet: str) -> str:
    """The first word of a bullet, lowercased, or "" if it has none."""
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", bullet.strip())
    return words[0].lower() if words else ""


def find_repeated_openers(bullets: list[str]) -> dict[str, list[int]]:
    """{opener: [bullet indexes]} for openers used >= the threshold.

    Scoped to the bullets of ONE role by the caller: repeating "Led" once in
    each of four jobs is a career, not a writing defect.
    """
    seen: dict[str, list[int]] = {}
    for index, bullet in enumerate(bullets):
        word = opener(bullet)
        if word:
            seen.setdefault(word, []).append(index)
    return {
        word: idxs for word, idxs in seen.items()
        if len(idxs) >= REPEATED_OPENER_THRESHOLD
    }


# ------------------------------------------------------- tense mismatch ---
#
# NARROWED FROM THE PLAN, deliberately. Part 3 specified "past-tense verbs in
# a role marked current, OR present tense in a finished one". The first half
# is not a defect: writing completed achievements in the past tense inside a
# current role is standard advice, so flagging it would mark correct resumes
# wrong -- the exact failure Tier 1 exists to avoid. Only the second half is
# unambiguous: "Manage a team of five" under a job that ended in 2019.
#
# Verbs spelled the same in both tenses (cut, set, put, read, cost, hit,
# spread, hurt, shut, let, split, broadcast) are excluded -- "Cut deploy time
# by 65%" under a finished role is past tense and must not be flagged.
_AMBIGUOUS_TENSE = {
    "cut", "set", "put", "read", "cost", "hit", "spread", "hurt", "shut",
    "let", "split", "broadcast", "cast", "bet", "quit", "upset",
}

PRESENT_TENSE_OPENERS: frozenset[str] = frozenset({
    "lead", "manage", "own", "drive", "direct", "head", "coordinate",
    "oversee", "mentor", "coach", "guide", "supervise",
    "build", "create", "design", "develop", "engineer", "architect",
    "implement", "launch", "ship", "deliver", "deploy", "establish",
    "introduce", "produce", "author", "write", "prototype", "maintain",
    "improve", "optimise", "optimize", "refactor", "modernise", "modernize",
    "streamline", "simplify", "consolidate", "standardise", "standardize",
    "restructure", "migrate", "upgrade", "automate", "harden", "secure",
    "transform", "tune", "port", "orchestrate",
    "reduce", "eliminate", "save", "accelerate", "scale", "grow", "expand",
    "increase", "generate", "recover", "fix", "resolve",
    "analyse", "analyze", "measure", "evaluate", "research", "investigate",
    "diagnose", "identify", "negotiate", "partner", "collaborate",
    "present", "influence", "run", "train", "support", "monitor", "review",
    "test", "handle", "ensure", "provide", "work", "contribute",
}) - _AMBIGUOUS_TENSE


def role_has_ended(entry: dict, date_label: str) -> bool:
    """True when the role is definitely over.

    Needs positive evidence. A role with no dates at all is NOT treated as
    finished -- `experience.missing_dates` already covers that, and guessing
    here would stack a second penalty on one missing field.
    """
    if entry.get("current"):
        return False
    label = (date_label or "").strip()
    if not label:
        return False
    return "present" not in label.lower() and "current" not in label.lower()


def find_present_tense(bullets: list[str]) -> list[int]:
    """Indexes of bullets that open in the present tense."""
    return [
        i for i, bullet in enumerate(bullets)
        if opener(bullet) in PRESENT_TENSE_OPENERS
    ]
