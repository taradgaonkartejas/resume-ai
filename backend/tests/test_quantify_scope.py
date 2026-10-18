"""The quantify writer may not invent the thing being counted.

Two live failures motivated this, both reported from the user's own resume:

    "Adds claim of deployment across specific number of countries not
     supported by resume"
    "Adds claim of serving unspecified number of users, which is not
     supported by the resume"

Neither contains a digit, so `new_digits` was blind to both. The placeholder
count was correct in both. They satisfied every rule `check_draft` enforced
and still asserted something the resume never says -- a blank in front of a
noun does not make the noun true.

There were TWO causes and both are covered here:

  1. nothing enforced "rephrase only what the line already claims", though
     the QUANTIFIER prompt asked for it;
  2. the writer was actively TOLD to invent, because `fix_hint` -- card copy
     listing example dimensions, "team size, users served, latency cut" --
     was passed to the model verbatim as "Guidance:".

`countries`, `users`, and `team size` appear nowhere in any of the three real
resumes. `latency` appears in exactly one.
"""

from __future__ import annotations

import pathlib

import pytest

from app.ai import prompts
from app.services import placeholders

SUMMARY = (
    "Full-stack engineer with 2+ years building and scaling production backend "
    "services for a global point-cloud / geospatial platform. Builds "
    "microservices across Node.js, Python (FastAPI), and Java / Spring Boot."
)
BULLET = "Deployed the platform using Docker and MinIO"
P = placeholders.PLACEHOLDER


class TestTheReportedFailures:
    def test_serving_an_invented_audience_is_rejected(self):
        draft = SUMMARY[:-1] + f", serving {P} users."
        complaint = placeholders.check_draft(SUMMARY, draft)
        assert complaint is not None
        assert "users" in complaint

    def test_an_invented_geography_is_rejected(self):
        draft = f"Deployed the platform across {P} countries"
        complaint = placeholders.check_draft(BULLET, draft)
        assert complaint is not None
        assert "countries" in complaint

    def test_the_complaint_tells_the_writer_what_to_do_instead(self):
        """It is fed back as attempt two, so it has to be actionable.

        "Rejected" alone would just buy a second identical draft.
        """
        draft = f"Deployed the platform across {P} countries"
        complaint = placeholders.check_draft(BULLET, draft)
        assert "already claims" in complaint.lower()

    def test_the_reported_word_carries_no_trailing_punctuation(self):
        """`_WORD_RE` keeps dots so "Node.js" survives as one token, which
        made a sentence-final word arrive as `users.` -- quoted back at the
        model, that reads like a different token than the one it wrote."""
        draft = SUMMARY[:-1] + f", serving {P} users."
        assert '"users"' in placeholders.check_draft(SUMMARY, draft)


class TestGoodDraftsStillPass:
    """The guard is strict, so the false-positive surface is pinned hard.

    A complaint is retryable rather than fatal -- `rewrite_for_finding` feeds
    it back and returns attempt two regardless -- so a false positive costs
    one extra model call. That is what makes strictness affordable, but it is
    not a licence to reject good work.
    """

    @pytest.mark.parametrize(
        "original,draft",
        [
            (
                "Improved processing performance for point-cloud datasets",
                f"Improved processing performance for point-cloud datasets by {P}%",
            ),
            (
                "Built microservices across Node.js and Python",
                f"Built {P} microservices across Node.js and Python",
            ),
            # plural of a word the line already uses
            ("Scaled the backend service", f"Scaled the backend services by {P}x"),
            ("Reduced build failures", f"Reduced build failures by {P}%"),
            # pure grammar added around the blank
            ("Cut deployment time", f"Cut deployment time by up to {P}%"),
        ],
    )
    def test_quantifying_an_existing_claim_is_allowed(self, original, draft):
        assert placeholders.check_draft(original, draft) is None


class TestTheOlderRulesStillHold:
    def test_a_new_digit_is_still_caught(self):
        assert placeholders.check_draft(
            "Improved performance", f"Improved performance by 40% and {P}"
        )

    def test_a_missing_placeholder_is_still_caught(self):
        assert placeholders.check_draft(
            "Improved performance", "Improved performance considerably"
        )

    def test_two_placeholders_are_still_caught(self):
        assert placeholders.check_draft(
            "Improved performance", f"Improved {P} performance by {P}%"
        )


class TestTheGuidanceNoLongerNamesDimensions:
    def test_the_card_copy_still_helps_the_human(self):
        """The menu is good UI -- it is only bad as a model instruction.

        Stripping it from the card to fix the writer would have been the
        wrong trade, so the card keeps it.
        """
        from app.services import heuristics

        src = pathlib.Path(heuristics.__file__).read_text()
        assert "users served" in src

    def test_the_writer_is_told_the_rule_not_the_menu(self):
        """agents.py must substitute quantify guidance rather than forward
        `fix_hint`. Asserted against the source because the alternative is a
        live model call."""
        from app.ai import agents

        src = pathlib.Path(agents.__file__).read_text()
        assert "Do not introduce a new thing to count" in src
        # and it must be conditional on the quantify mode, not global
        assert "if quantifying and not composing:" in src

    def test_the_prompt_and_the_guard_agree(self):
        """Prompt and enforcement must say the same thing.

        A prompt that forbids more than the guard checks is how this bug
        survived; a guard that forbids more than the prompt states is how a
        writer gets stuck in a loop it cannot reason its way out of.
        """
        q = prompts.QUANTIFIER.lower()
        assert "never invent" in q and "scope" in q


class TestAgainstTheRealResumes:
    """The invented dimensions are absent from the actual documents."""

    @pytest.mark.parametrize("word", ["countries", "users", "team size"])
    def test_the_invented_dimensions_appear_in_no_real_resume(self, word):
        from app.services.parsing import extract_text

        pdfs = sorted(pathlib.Path("/home/user/uploads").glob("*.pdf"))
        if not pdfs:
            pytest.skip("real resumes not present in this environment")
        for pdf in pdfs:
            text = extract_text(pdf.name, pdf.read_bytes()).lower()
            assert word not in text, f"{word!r} found in {pdf.name}"
