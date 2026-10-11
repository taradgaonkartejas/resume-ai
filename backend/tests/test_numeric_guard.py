"""The deterministic guard against invented numbers.

Written after the first fabrication this project ever observed. Running
`writer-report-llm` on the compose path (`summary_absent`: the summary is
empty and must be built from the experience below it), the writer produced a
summary containing **58**. That digit appears nowhere in the resume; the
nearest real figure is the CI bullet, 22 minutes -> 9 minutes, which is a
59.1% reduction.

So the model either invented the number outright or tried to derive a
percentage and got the arithmetic wrong. The second reading is the more
unsettling one: a derived metric feels safe because it is arithmetic on real
figures, but the result was a false quantitative claim about the candidate's
own work, in their own resume, under their name.

The LLM critic did reject that draft, so nothing false would have reached the
document. But that made a single LLM call the only thing standing between a
fabricated figure and the user's resume -- a probabilistic guard on this
project's most important safety property. These tests cover the deterministic
one now sitting in front of it.

Key design point: numbers are compared against the WHOLE resume, not against
the one string being rewritten. Composing a summary from an empty string must
still be allowed to surface "22 minutes to 9 minutes" from a bullet below it;
those are the candidate's own facts.
"""

from app.ai import agents
from app.ai.agents import unverifiable_numbers

RESUME_TEXT = (
    "Reduced CI pipeline runtime from 22 minutes to 9 minutes. "
    "Led 8 engineers. Shipped in FY2019. Ran workloads on AWS S3 and EC2."
)

DATA = {
    "summary": {"text": ""},
    "experience": [
        {
            "company": "Acme",
            "role": "Platform Engineer",
            "dates": "",
            "bullets": [
                "Reduced CI pipeline runtime from 22 minutes to 9 minutes.",
                "Built Terraform modules adopted by 8 teams.",
            ],
        }
    ],
}


# --------------------------------------------------------------- the predicate
def test_the_observed_fabrication_is_caught():
    """The actual failure: 58, from a resume whose real figure is 59.1%."""
    assert unverifiable_numbers(RESUME_TEXT, "Cut CI runtime by 58%.") == {"58"}


def test_a_metric_surfaced_from_elsewhere_in_the_resume_is_allowed():
    """The compose path's entire job. These are the candidate's own numbers."""
    drafted = "Platform engineer who cut CI runtime from 22 minutes to 9 minutes."
    assert unverifiable_numbers(RESUME_TEXT, drafted) == set()


def test_rewording_that_preserves_a_metric_is_allowed():
    assert unverifiable_numbers(RESUME_TEXT, "Cut CI runtime 22 -> 9 minutes.") == set()


def test_text_with_no_numbers_is_allowed():
    assert unverifiable_numbers(RESUME_TEXT, "Platform engineer.") == set()


# ------------------------------------------------- names are not measurements
def test_digits_glued_to_the_end_of_a_word_are_names_not_claims():
    """S3, EC2, v2, OAuth2 are identifiers. Flagging them would be noise."""
    assert unverifiable_numbers(RESUME_TEXT, "Migrated to S3 and EC2.") == set()
    assert unverifiable_numbers(RESUME_TEXT, "Shipped v2 of the pipeline.") == set()


def test_a_single_digit_inside_a_name_does_not_license_a_claim():
    """The leak this guard would otherwise have.

    "S3" contains a 3. If that licensed every standalone 3, the guard would be
    useless for exactly the small numbers that are easiest to invent.
    """
    assert unverifiable_numbers(RESUME_TEXT, "Made the build 3x faster.") == {"3"}
    assert unverifiable_numbers(RESUME_TEXT, "Saved 5k engineer-hours.") == {"5"}


def test_a_multi_digit_run_inside_a_token_is_a_real_figure():
    """FY2019 should license 2019 -- that is the same fact, written twice."""
    assert unverifiable_numbers(RESUME_TEXT, "Shipped in 2019.") == set()


def test_decimal_formatting_is_not_a_fabrication():
    assert unverifiable_numbers(RESUME_TEXT, "Cut runtime by 9.0 minutes.") == set()


def test_a_genuinely_new_decimal_is_a_fabrication():
    assert unverifiable_numbers(RESUME_TEXT, "Cut runtime by 9.4 minutes.") == {"9.4"}


# ------------------------------------------------------- wired into the path
def _draft(suggested: str) -> dict:
    return {
        "target_ref": "exp_0.bullet_0",
        "original_text": DATA["experience"][0]["bullets"][0],
        "suggested_text": suggested,
        "keywords": [],
    }


def test_hard_check_blocks_an_invented_number_without_any_llm():
    """It must run with or without a key -- that is the point of hard checks."""
    outcome = agents._hard_check(_draft("Cut CI runtime by 58%."), DATA)
    assert outcome is not None, "an invented number must not reach the critic"
    assert outcome.value.approved is False
    assert outcome.value.severity == "fabrication"
    assert "58" in outcome.value.notes


def test_hard_check_passes_a_faithful_rewrite():
    outcome = agents._hard_check(
        _draft("Cut CI pipeline runtime from 22 minutes to 9 minutes."), DATA
    )
    assert outcome is None, "a faithful rewrite must reach the critic normally"


def test_the_guard_does_not_depend_on_the_llm_critic_running():
    """Regression for the gap that prompted this.

    The equivalent numeric check already existed in `_rule_critique`, but that
    only runs when the LLM critic does NOT -- so in normal operation nothing
    deterministic stood between an invented figure and the resume.
    """
    import inspect

    source = inspect.getsource(agents._hard_check)
    assert "unverifiable_numbers" in source
