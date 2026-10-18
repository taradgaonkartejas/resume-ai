"""The two tabs must price a change in the same currency.

Keyword match answered "does this look like the job ad". It could not answer
"does accepting this make my resume better or worse" -- a different question,
and the only one the Analysis tab was equipped to ask. A user could accept
six suggestions, watch keyword match hit 100%, and have no idea what it did
to their score.

`score_delta` closes that: the same rule engine, the same arithmetic, so a
`+2` on a tailor card and a `+2` on an analysis card mean the same thing.
"""

from __future__ import annotations

import copy

from app.services.heuristics import data_fingerprint, score_resume
from app.services.suggestion_service import _delta_for
from tests import writer_cases as W


def _base():
    return copy.deepcopy(W.BASE)


def test_delta_matches_the_analysis_arithmetic_exactly():
    """The number on a tailor card must equal a real rescore, not an estimate.

    Pinned against score_resume directly: if the two ever diverge, one tab is
    lying to the user about what a point is.
    """
    data = _base()
    base = score_resume(data)["overall_score"]
    ref = "exp_0.bullet_0"
    better = "Cut CI pipeline runtime from 22 minutes to 9 minutes for 8 teams."

    delta = _delta_for(data, ref, better, base)

    from app.services import resume_ops
    expected = (
        score_resume(resume_ops.apply_patch(data, ref, better))["overall_score"]
        - base
    )
    assert delta == expected


def test_a_harmless_keyword_swap_is_zero_not_missing():
    """Most tailoring is score-neutral. Saying so is better than hiding it."""
    data = _base()
    base = score_resume(data)["overall_score"]
    original = data["experience"][0]["bullets"][0]
    delta = _delta_for(data, "exp_0.bullet_0", original, base)
    assert delta == 0


def test_a_vanished_ref_prices_zero_instead_of_exploding():
    data = _base()
    base = score_resume(data)["overall_score"]
    assert _delta_for(data, "exp_99.bullet_99", "anything", base) == 0
    assert _delta_for(data, "exp_0.bullet_0", "", base) == 0


def test_an_accepted_suggestion_is_priced_against_what_it_replaced():
    """An accepted suggestion is already IN the resume.

    Scoring it forward is a no-op and would print "no score change" on the
    very cards that earned the points. `revert_to` prices it backwards.
    """
    data = _base()
    ref = "exp_0.bullet_1"
    weak = "Responsible for the build pipeline."
    data["experience"][0]["bullets"][1] = weak
    weak_score = score_resume(data)["overall_score"]

    strong = "Built Terraform modules adopted by 8 engineering teams."
    from app.services import resume_ops
    applied = resume_ops.apply_patch(data, ref, strong)
    applied_score = score_resume(applied)["overall_score"]

    # priced backwards from the applied state
    delta = _delta_for(applied, ref, strong, applied_score, revert_to=weak)
    assert delta == applied_score - weak_score


# ------------------------------------------------------------- fingerprint
def test_fingerprint_changes_when_the_resume_does():
    data = _base()
    before = data_fingerprint(data)
    data["summary"]["text"] = data["summary"]["text"] + " Extra."
    assert data_fingerprint(data) != before


def test_fingerprint_is_stable_across_key_order():
    """Prose goes stale when CONTENT changes, not when a dict is rebuilt.

    Without sort_keys the flag would fire on every save and the staleness
    notice would become noise the user learns to ignore.
    """
    a = {"summary": {"text": "x"}, "experience": []}
    b = {"experience": [], "summary": {"text": "x"}}
    assert data_fingerprint(a) == data_fingerprint(b)
