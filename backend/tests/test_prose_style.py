"""Tier 1 prose-style checks: complex sentences and AI-fingerprint language.

Requested 2026-10-25 from the ResumeWorded-style step-5 report the user
screenshotted: "Complex and lengthy Sentences Detected" and "AI language
Detected". The checks are SCORED (owner direction), so they live in
quality_rules.py and are wired through SUB_BUDGETS inside the summary and
experience categories -- the 100-point total does not move, existing checks
give up points (see the 2026-10-25 re-slice comment in heuristics.py).

The Tailor side of the ask is the critic gate: a rewrite that INTRODUCES
AI-tell words the scorer penalises would fix one card by creating another,
so _rule_critique rejects it before it reaches a card.
"""

from __future__ import annotations

from app.ai.agents import _rule_critique
from app.services import quality_rules as q

# ------------------------------------------------------------ detectors ----

class TestComplexSentences:

    def test_short_plain_sentences_are_silent(self):
        assert q.find_complex_sentences(
            "Built Terraform modules. Adopted by 8 teams."
        ) == []

    def test_a_comma_pair_in_a_short_bullet_is_not_complex(self):
        # "A, cutting B" is how a bullet is supposed to read.
        assert q.find_complex_sentences(
            "Led migration of 40 services to Kubernetes, cutting deploy time by 65%"
        ) == []

    def test_sheer_length_flags_a_sentence(self):
        sentence = " ".join(["handled the platform rollout"] + ["work"] * 30)
        assert q.find_complex_sentences(sentence)

    def test_clause_packing_flags_a_midsize_sentence(self):
        flagged = q.find_complex_sentences(
            "Full Stack Engineer with 2+ years of experience in full-stack "
            "development, microservices architecture, and cloud-native "
            "deployment across three distinct product lines."
        )
        assert flagged and "microservices" in flagged[0]

    def test_the_evidence_is_the_sentence_itself(self):
        # The card quotes the finding; it must be text the user can find.
        text = "Short one. " + " ".join(["work"] * 33) + "."
        hits = q.find_complex_sentences(text)
        assert len(hits) == 1 and hits[0] in text


class TestAILanguage:

    def test_classic_llm_tells_fire(self):
        hits = q.find_ai_language(
            "Seamlessly leveraged robust solutions in today's fast-paced world."
        )
        assert "seamlessly" in hits and "leveraged" in hits

    def test_longer_phrase_counts_once(self):
        hits = q.find_ai_language("in today's fast-paced world")
        assert hits == ["in todays fast paced world"]

    def test_word_boundaries_prevent_false_hits(self):
        # "dynamics" is not "dynamic"; "adequate" is not "adept".
        assert q.find_ai_language(
            "Handled team dynamics and kept throughput adequate."
        ) == []

    def test_credited_action_verbs_are_never_flagged(self):
        # The scorer credits these openers; punishing them too would be
        # incoherent. Guard the lexicon against drift into that list.
        from app.services.heuristics import ACTION_VERBS

        verbs = " ".join(sorted(ACTION_VERBS))
        assert q.find_ai_language(verbs) == [], (
            f"lexicon overlaps credited action verbs: {q.find_ai_language(verbs)}"
        )

    def test_plain_work_language_is_silent(self):
        assert q.find_ai_language(
            "Reduced pager volume by 45% by rewriting alert rules around "
            "service level objectives."
        ) == []


# ----------------------------------------------------------- critic gate ----

class TestCriticGate:

    def test_a_rewrite_introducing_ai_language_is_rejected(self):
        verdict = _rule_critique(
            "Built the deployment pipeline.",
            {"suggested_text": "Seamlessly orchestrated a robust deployment pipeline."},
        )
        assert verdict.approved is False
        assert "seamlessly" in verdict.notes

    def test_ai_language_already_present_is_the_users_choice(self):
        # The original already says it; a faithful reword may keep it.
        verdict = _rule_critique(
            "Leveraged Kafka to cut event lag by 40%.",
            {"suggested_text": "Leveraged Kafka, cutting event lag by 40%."},
        )
        assert verdict.approved is True

    def test_a_plain_faithful_rewrite_still_passes(self):
        verdict = _rule_critique(
            "Built the deployment pipeline used by 8 teams.",
            {"suggested_text": "Built the deployment pipeline now used by 8 teams."},
        )
        assert verdict.approved is True
