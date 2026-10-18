"""`_METRIC_RE` must recognise the "N+" form.

Found while checking why `summary.no_metric` fired on a summary that opens
"Full-stack engineer with 2+ years...". The regex handled "2 years" but not
"2+ years", so the two spellings of one fact disagreed:

    2 years        MATCH          2+ years        MISS
    Led 8 teams    MATCH          Led 10+ teams   MISS

"N+" is one of the most common ways a number appears on a resume, so
"Led 10+ engineers" was being told it contained no quantified achievement.

Note on scope: this makes the two spellings CONSISTENT, it does not decide
whether tenure counts as an achievement. "2 years" already cleared
`summary.no_metric` before this change; "2+ years" now does too. Whether
stating your own years of experience should satisfy that check at all is a
separate product question, and changing it would affect both forms.
"""

from __future__ import annotations

import pytest

from app.services.heuristics import _METRIC_RE


class TestThePlusForm:
    @pytest.mark.parametrize(
        "text",
        [
            "2+ years",
            "5+ years of experience",
            "10+ engineers",
            "Led 10+ projects to completion",
            "Scaled to 10k+ requests",
            "Improved throughput by 20+%",
        ],
    )
    def test_plus_suffixed_figures_count(self, text):
        assert _METRIC_RE.search(text), f"{text!r} should read as quantified"

    @pytest.mark.parametrize(
        "bare,plussed",
        [
            ("2 years", "2+ years"),
            ("8 teams", "8+ teams"),
            ("40%", "40+%"),
            ("40k", "40k+"),
        ],
    )
    def test_both_spellings_agree(self, bare, plussed):
        """The bug was disagreement, not absence. Pinning the pair keeps a
        future edit from reopening the gap on one side only."""
        assert bool(_METRIC_RE.search(bare)) == bool(_METRIC_RE.search(plussed))


class TestTheYearGuardSurvives:
    """A calendar year is not a metric, and the `+` must not smuggle one in."""

    @pytest.mark.parametrize("text", ["in 2024", "since 2019", "2020 - 2023"])
    def test_calendar_years_are_still_not_metrics(self, text):
        assert not _METRIC_RE.search(text)

    def test_a_bare_version_number_is_still_not_a_metric(self):
        assert not _METRIC_RE.search("version 2")


class TestOnTheRealResumes:
    def test_the_reported_summary_now_reads_as_quantified(self):
        summary = (
            "Full-stack engineer with 2+ years building and scaling production "
            "backend services for a global point-cloud / geospatial platform."
        )
        assert _METRIC_RE.search(summary)

    def test_an_unquantified_line_is_still_unquantified(self):
        """The guard against the fix going too far."""
        assert not _METRIC_RE.search(
            "Responsible for backend development and code reviews"
        )
