"""Turning job-description gaps into questions the user can answer.

`TailoringSession.gap_keywords` already knows every keyword the job asked for
that the resume cannot support. Until now that list terminated in a grey chip.
This module turns the useful part of it into questions.

Everything here is deterministic. No model is involved in deciding WHAT to ask
or HOW to word it, for two reasons:

  * A generated question is a fifth model call whose only possible failure is
    a worse question. There is no upside to buy with the tokens.
  * A job description is untrusted text. A model asked to write questions
    from it is a prompt-injection surface pointed directly at the user, and
    the output is a sentence the user is being invited to agree with.
"""

import re

from app.services.text_match import canonical_aliases

# "3+ years", "5 years of", "2-4 years". The JD attaching a duration to a
# skill is what turns a yes/no question into one that needs real text.
_YEARS_RE = re.compile(r"(\d+)\s*(?:\+|-\s*\d+)?\s*years?", re.I)

# A duration is attached to a keyword only within the SAME LINE.
#
# This started as a +/-60 character window and was wrong on the very first
# real job description. Given
#
#     - Experience with GitOps and progressive delivery
#     - 3+ years with Redis or a comparable caching layer
#
# the window reached across the newline and reported that the job wanted
# "3+ years of GitOps" -- a requirement the employer never wrote, shown to
# the user as a quote from the posting. Job descriptions are bullet lists,
# and a bullet is the unit a requirement is written in.
def _lines_containing(text: str, needle: str) -> list[str]:
    lowered = needle.lower()
    return [ln for ln in text.splitlines() if lowered in ln.lower()]

# A gap list is long and mostly noise; a wall of questions is ignored wholesale.
MAX_QUESTIONS = 4


def is_skill(keyword: str) -> bool:
    """Is this a thing a person can claim to have done?

    The filter the whole feature depends on. `extract_keywords` is a word
    counter with a stopword list, so a real job description yields gaps like
    `across`, `define`, `looking`, `own` and `mean` alongside `gitops`.
    Asking "do you have experience with 'across'?" would discredit the
    feature on its first use.

    Membership in SKILL_TAXONOMY is the test, because the taxonomy is the
    one place in this codebase that already asserts "this string names a
    skill" -- it is what lets `k8s` match `Kubernetes`. Reusing it means the
    question filter and the matcher can never disagree about what a skill is.

    The cost is that the taxonomy is small (18 entries), so genuine skills
    outside it produce no question. That is the right direction to fail:
    a missing question is invisible, an absurd one is not.
    """
    lowered = keyword.strip().lower()
    if not lowered:
        return False
    # canonical_aliases returns [term] unchanged when nothing matches, and the
    # canonical spelling plus aliases when it does.
    hits = canonical_aliases(lowered)
    return len(hits) > 1 or hits[0].lower() != lowered


def _duration_near(jd_text: str, keyword: str) -> str:
    """The duration the JD attaches to this keyword, or "" if none.

    Same line only. See the note above `_lines_containing`.
    """
    for spelling in canonical_aliases(keyword):
        for line in _lines_containing(jd_text, spelling):
            found = _YEARS_RE.search(line)
            if found:
                return found.group(0).strip()
    return ""


def _label(keyword: str) -> str:
    """The canonical spelling, so the card says GitOps and not gitops."""
    return canonical_aliases(keyword)[0]


def question_for(keyword: str, jd_text: str) -> dict:
    """One question. Wording is fixed; only the skill and duration vary.

    The reference screenshot asks "Do you have experience utilizing GitLab
    CI/CD pipelines in a professional environment?" -- a question with its
    preferred answer printed on it. It invites `yes` from anyone skimming,
    and `yes` is the answer that makes work appear on screen.

    So these ask for EVIDENCE rather than CONFIRMATION, and state the
    do-nothing option explicitly. "Leave this blank" is a real, named,
    unembarrassing choice, which is the only way a no-answer stays as easy
    to give as a yes.
    """
    label = _label(keyword)
    duration = _duration_near(jd_text, keyword)

    if duration:
        text = (
            f"This job asks for {duration} of {label}, which does not appear "
            f"in your resume. If you have that experience, say where - which "
            f"role, and how long. If you do not, leave this blank and reject "
            f"the card."
        )
    else:
        text = (
            f"{label} appears in this job description but not in your resume. "
            f"If you have used it, say where - which role, and what you did "
            f"with it. If you have not, leave this blank and reject the card."
        )

    return {
        "keyword": keyword,
        "label": label,
        "question": text,
        # A duration claim can never be made by a button. "Yes" to "3+ years
        # of X" attests a specific number the user never typed, and that
        # number would then be laundered into the resume as their own word.
        "requires_text": bool(duration),
    }


def questions_for_gaps(gap_keywords: list[str], jd_text: str) -> list[dict]:
    """The questions worth asking, highest-value first.

    Duration-qualified gaps sort first: the job description named a bar, so
    the gap is explicit rather than inferred, and it is the one the recruiter
    is most likely to filter on.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for keyword in gap_keywords:
        if not is_skill(keyword):
            continue
        label = _label(keyword).lower()
        # Two JD spellings of one skill ("ci", "pipelines") are one question.
        if label in seen:
            continue
        seen.add(label)
        out.append(question_for(keyword, jd_text))

    out.sort(key=lambda q: (not q["requires_text"], q["label"].lower()))
    return out[:MAX_QUESTIONS]


def answer_supports(answer: str, keyword: str) -> bool:
    """Does this answer actually attest the skill it was asked about?

    The failure this exists to stop is the vague save: the user declines, or
    types something unrelated, and a suggestion appears anyway reading
    "familiar with GitOps". An answer that does not name the thing is not an
    attestation of it, and the correct outcome is no suggestion at all -- not
    a softened one.

    Deliberately shallow. It checks that the skill is named, under any
    spelling the taxonomy knows. Judging whether "I set up ArgoCD once in a
    side project" is resume-worthy is the user's call, not a regex's.
    """
    if not answer or not answer.strip():
        return False
    from app.services.text_match import contains

    return any(contains(answer, spelling) for spelling in canonical_aliases(keyword))
