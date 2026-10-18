"""The critic must understand the `[__]` quantify blank.

Background. The quantify path exists because a writer must never guess a
number: it restructures the line and leaves `[__]` for the candidate. The
critic, meanwhile, rejects any rewrite that "invents or alters a metric".
Those two rules were in direct contradiction -- the critic had never been
told the convention existed, so a correct quantify draft was approved only
by luck, and a live run produced:

    422 {"reason": "critic_rejected",
         "detail": "... Adds claim of deployment across specific number of
                    countries not supported by resume"}

These tests pin the contract from the side that does not need a model. The
corpus cases in `critic_cases.py` exercise the live judgement; these
exercise the thing that silently broke, which was the PROMPT.
"""

from __future__ import annotations

import re

from app.ai import prompts
from app.services import placeholders


def _critic() -> str:
    return prompts.CRITIC


class TestTheCriticKnowsTheConvention:
    def test_the_prompt_names_the_actual_placeholder_token(self):
        """The literal token, not a description of it.

        `CRITIC` is handed to the model raw (agents.py), so it cannot carry a
        `{placeholder}` format slot -- it ends with a JSON example whose
        braces would need escaping. The token is therefore spelled out, and
        this test is what keeps the spelling honest: change
        `placeholders.PLACEHOLDER` and this fails rather than leaving the
        critic quietly describing a token that no longer exists.
        """
        assert placeholders.PLACEHOLDER in _critic()

    def test_the_prompt_says_the_blank_is_not_a_metric(self):
        """The specific contradiction that caused the bug.

        The rejection list contains "invents or alters a metric". Without an
        explicit carve-out, that rule alone condemns every quantify draft.
        """
        text = _critic().lower()
        assert "not a metric" in text or "is not a figure" in text

    def test_the_prompt_gives_both_verdicts_not_just_approval(self):
        """A carve-out that only ever says "approve" is worse than none.

        It would turn the critic into a rubber stamp for anything containing
        a blank -- which is exactly how `across [__] countries` would get
        through. The prompt must show the rejecting case too.
        """
        text = _critic().lower()
        assert "approve when" in text
        assert "reject when" in text

    def test_the_rejecting_example_is_the_dimension_case(self):
        """The failure actually observed in production, kept as the example.

        A worked example of the real mistake is worth more to a model than an
        abstract rule, and it documents the incident for the next reader.
        """
        text = _critic().lower()
        assert "countries" in text

    def test_the_approving_example_quantifies_an_existing_claim(self):
        """The counterpart: a blank that only sizes what the line already says."""
        critic = _critic()
        # Some example pairs a bare percent sign with the blank.
        assert re.search(re.escape(placeholders.PLACEHOLDER) + r"\s*%", critic)


class TestThePromptStaysUsable:
    def test_critic_has_no_format_slots(self):
        """It is passed to the model raw; a `{slot}` would reach the model
        verbatim, and `.format()` would choke on the JSON example's braces.
        """
        critic = _critic()
        # Ignore the JSON example object at the end -- those braces are data.
        slots = set(re.findall(r"\{([a-z_]+)\}", critic))
        assert slots == set(), f"unformatted slots would reach the model: {slots}"

    def test_the_json_contract_survived_the_edit(self):
        """Inserting prose above the reply format must not disturb it.

        Adding a section mid-prompt is exactly how a response contract gets
        orphaned, so the shape the parser depends on is asserted here.
        """
        critic = _critic()
        assert '"verdicts"' in critic
        assert '"approved"' in critic
        assert '"index"' in critic


class TestTheWriterAndCriticAgree:
    def test_both_sides_use_one_definition_of_the_placeholder(self):
        """Writer and critic must mean the same token.

        `QUANTIFIER` interpolates `placeholders.PLACEHOLDER`; `CRITIC` spells
        it out. Two spellings would mean the writer emits one blank and the
        critic polices another, which is unfixable from the UI.
        """
        quantifier = prompts.QUANTIFIER.format(
            context="", length_rule="", placeholder=placeholders.PLACEHOLDER
        )
        assert placeholders.PLACEHOLDER in quantifier
        assert placeholders.PLACEHOLDER in prompts.CRITIC

    def test_the_writer_is_still_told_not_to_invent_scope(self):
        """C2 is deferred, so the ONLY thing standing between the writer and
        another `across [__] countries` is this prompt line. If a future edit
        drops it, the deterministic check does not yet exist to catch the
        fallout -- so it is pinned here.
        """
        quantifier = prompts.QUANTIFIER.lower()
        assert "scope" in quantifier
        assert "never invent" in quantifier
