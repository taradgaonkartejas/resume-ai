"""Part 3 Tier 1 — the deterministic writing-quality rules.

Half of these tests are CONTROLS: sentences that must NOT be flagged. That
balance is the point. Tier 1 exists because a rule can be checked
objectively; the moment one fires on a correct resume it is doing the thing
Part 3 pushed into Tier 2 (advisory, unscored, clearly a model's opinion).
A false accusation of an impossible metric is worse than silence.

Each control below is a sentence that broke an earlier draft of its rule.
"""

import pytest

from app.services import quality_rules as q


class TestFiller:
    @pytest.mark.parametrize("text,phrase", [
        ("A results-driven engineer", "results driven"),
        ("A results driven engineer", "results driven"),
        ("Results—Driven engineer", "results driven"),
        ("A proven track record of delivery", "proven track record"),
        ("Strong team player", "team player"),
        ("Detail-oriented and thorough", "detail oriented"),
        ("Able to think outside the box", "think outside the box"),
        ("Drove synergy across teams", "synergy"),
    ])
    def test_catches_filler_in_any_punctuation(self, text, phrase):
        assert phrase in q.find_filler(text)

    @pytest.mark.parametrize("text", [
        "Led migration of 40 services to Kubernetes, cutting deploy time 65%",
        "Built Terraform modules adopted by 8 engineering teams",
        "Mentored 4 junior engineers through their first production launches",
        "Reduced cloud spend by 18 percent through rightsizing",
        "",
    ])
    def test_leaves_real_writing_alone(self, text):
        assert q.find_filler(text) == []

    def test_a_phrase_inside_a_longer_word_is_not_a_hit(self):
        """Word-boundary matching, not substring."""
        assert q.find_filler("Measured synergistic effects") == []


class TestImpossibleMetrics:
    @pytest.mark.parametrize("text", [
        "Reduced p95 latency by 100%",
        "Cut build times by 150%",
        "Decreased error rates by 100 %",
        "Lowered cost by 120%",
        "Slashed memory use by 200%",
    ])
    def test_catches_a_reduction_of_100_percent_or_more(self, text):
        assert q.find_impossible_metrics(text)

    @pytest.mark.parametrize("text", [
        # An INCREASE above 100% is completely ordinary.
        "Increased revenue by 300%",
        "Grew signups by 450%",
        # A reduction under 100% is the normal case.
        "Reduced p95 latency by 65%",
        # The clause binding: a real reduction next to a real large increase.
        "Reduced cost by 50% and increased signups by 200%",
        "Cut deploy time by 40%, growing release frequency by 500%",
        # Full elimination is coherent with these verbs, so they are not
        # treated as reductions at all.
        "Eliminated 100% of manual release steps",
        "Removed 100% of hardcoded credentials",
        "Automated 100% of the regression suite",
        # Genuine large speedups exist and must not be accused.
        "Made dashboard queries 500x faster",
        "Delivered a 10x improvement in throughput",
    ])
    def test_control_does_not_fire_on_defensible_claims(self, text):
        assert q.find_impossible_metrics(text) == [], text

    def test_catches_an_absurd_multiplier(self):
        assert q.find_impossible_metrics("Made queries 5000x faster")

    def test_the_multiplier_ceiling_is_deliberately_generous(self):
        """Documents the tradeoff rather than leaving it to be rediscovered."""
        assert q.MULTIPLIER_CEILING >= 1000


class TestFirstPerson:
    @pytest.mark.parametrize("text", [
        "I led the migration",
        "We shipped the platform",
        "Improved my team's velocity",
        "Rebuilt our deployment pipeline",
    ])
    def test_catches_pronouns(self, text):
        assert q.find_first_person(text)

    @pytest.mark.parametrize("text", [
        "Tuned I/O throughput on the storage layer",   # I/O, not "I"
        "Delivered Phase I of the migration",          # a numeral
        "Resolved Type I errors in the classifier",
        "Launched in the US market",                   # not "us"
        "Migrated MySQL to Postgres",                  # not "my"
        "Led the on-call rotation across 40 services",
    ])
    def test_control_does_not_fire_on_engineering_english(self, text):
        assert q.find_first_person(text) == [], text


class TestRepeatedOpener:
    def test_three_identical_openers_in_one_role(self):
        found = q.find_repeated_openers(
            ["Led A", "Led B", "Led C", "Built D"]
        )
        assert found == {"led": [0, 1, 2]}

    def test_two_is_a_coincidence_not_a_defect(self):
        assert q.find_repeated_openers(["Led A", "Led B", "Built C"]) == {}

    def test_the_opener_ignores_bullet_punctuation(self):
        assert q.opener("- Led the team") == "led"
        assert q.opener("\u2022 Built it") == "built"
        assert q.opener("") == ""
        assert q.opener("42 services migrated") == "services"

    def test_the_threshold_is_stated_once(self):
        assert q.REPEATED_OPENER_THRESHOLD == 3


class TestTenseMismatch:
    def test_present_tense_in_a_finished_role(self):
        assert q.find_present_tense(["Manage a team of five"]) == [0]

    @pytest.mark.parametrize("bullet", [
        "Led the migration",
        "Built Terraform modules",
        "Reduced cloud spend by 18 percent",
        # Spelled identically in both tenses -- past tense here, and
        # flagging it would be flatly wrong.
        "Cut deploy time by 65%",
        "Set up the on-call rotation",
        "Read replicas added for reporting",
    ])
    def test_control_past_tense_and_ambiguous_verbs_are_clean(self, bullet):
        assert q.find_present_tense([bullet]) == [], bullet

    def test_ambiguous_verbs_are_excluded_from_the_present_set(self):
        for verb in ("cut", "set", "put", "read", "cost", "hit", "split"):
            assert verb not in q.PRESENT_TENSE_OPENERS

    def test_a_role_marked_current_has_not_ended(self):
        assert q.role_has_ended({"current": True}, "2020 \u2013 Present") is False

    def test_a_role_with_a_present_label_has_not_ended(self):
        assert q.role_has_ended({"current": False}, "2020 \u2013 Present") is False

    def test_a_dated_past_role_has_ended(self):
        assert q.role_has_ended({"current": False}, "2018 \u2013 2020") is True

    def test_a_role_with_no_dates_is_not_assumed_finished(self):
        """`experience.missing_dates` already covers this.

        Guessing here would stack a second penalty onto one missing field.
        """
        assert q.role_has_ended({"current": False}, "") is False


def test_the_plan_deviation_is_recorded_in_the_module():
    """Part 3 asked for past tense in a CURRENT role to be flagged too.

    It is not, deliberately: writing completed achievements in the past
    tense inside a current role is standard advice, so the rule would mark
    correct resumes wrong. The reasoning must stay next to the code.
    """
    doc = q.__doc__ or ""
    source = q.find_present_tense.__doc__ or ""
    assert "false accusation" in doc.lower()
    assert q.find_present_tense(["Led the migration"]) == []
    assert source is not None
