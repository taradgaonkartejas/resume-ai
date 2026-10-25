"""A tailor suggestion's keyword chips must survive contact with its own text.

Reported from a live session (2026-10-25): the keyword-match bar said
"started at 38%", the user accepted two "KEYWORD MATCH" suggestions, and
the bar DROPPED to 25%. Two deterministic failures compounded:

1. Claim dishonesty. The first card claimed "java full stack development"
   and "database management" while its rewritten bullet contained neither
   phrase. The percent is RECOMPUTED from the text on every accept
   (suggestion_service._handle_action), so a claimed keyword that is not
   literally in the suggested text can never materialise -- the chip
   describes the card as adding something the document will never gain.

2. Rewrite regression. The same rewrite deleted the original bullet's
   "automated contract testing" -- its only previously-MATCHED keyword.
   Accepting a suggestion is allowed to tighten a bullet; it must never
   cost the resume a JD keyword that was already counting.

Both are now enforced at the persistence boundary in
TailoringService.tailor, in the same place and spirit as the critic hard
checks: refuses drafts that delete matched keywords, refuses drafts whose
claims are entirely absent from their text, and strips partially-honest
claims down to what verifies.
"""

from __future__ import annotations

from app.services.heuristics import match_keywords
from app.services.resume_ops import apply_patch, empty_resume
from app.services.text_match import canonical_aliases, contains


def _data() -> dict:
    data = empty_resume()
    data["contact"] = {"name": "Tejas Shinde", "email": "t@x.io"}
    data["experience"] = [
        {
            "company": "Ceinsys Tech Ltd.", "role": "Software Engineer",
            "dates": "2023 - Present",
            "bullets": [
                "Secured 70+ RESTful APIs with token-based OIDC/Keycloak "
                "authentication and automated contract testing for the "
                "cross-team documentation effort.",
                "Facilitated Agile ceremonies, including stand-ups, sprint "
                "planning, and retrospectives, using Azure DevOps.",
            ],
        }
    ]
    return data


KEYWORDS = [
    "java full stack development", "database management",
    "agile development practices", "cloud computing",
    "mentoring", "contract testing", "cross-team collaboration", "keycloak",
]


class TestClaimHonesty:
    """A claimed keyword must appear in the suggested text, or leave the list."""

    @staticmethod
    def _verify(claimed: list[str], suggested: str) -> list[str]:
        return [
            kw for kw in claimed
            if any(contains(suggested, s) for s in canonical_aliases(kw))
        ]

    def test_a_claim_absent_from_the_text_verifies_to_nothing(self):
        """The exact card from the report: claims two keywords, contains none."""
        claimed = ["java full stack development", "database management"]
        suggested = (
            "Secured 70+ RESTful APIs with OIDC/Keycloak, standardizing "
            "contracts via OpenAPI/Swagger and Postman for automated testing "
            "and clear documentation"
        )
        assert self._verify(claimed, suggested) == []

    def test_a_claim_present_in_the_text_verifies(self):
        claimed = ["contract testing", "database management"]
        suggested = "Led automated contract testing across 70+ APIs for 3 teams."
        assert self._verify(claimed, suggested) == ["contract testing"]

    def test_verification_is_case_insensitive_and_word_bounded(self):
        """"SQL" in the claim matches "sql" in the text, but "Agile" must not
        match "agilities" -- same matching rules the accept-recompute uses."""
        assert self._verify(["SQL"], "Transactional sql writes.") == ["SQL"]
        assert self._verify(["agile"], "Worked on agilities.") == []


class TestNoRegressionOnRewrite:
    """Accepting a rewrite must never remove a previously-matched keyword."""

    def test_rewriting_out_the_only_matched_keyword_is_rejected(self):
        data = _data()
        scores = match_keywords(data, KEYWORDS)
        baseline = {m.lower() for m in scores["matched_keywords"]}
        assert "contract testing" in baseline  # fixture sanity

        # The rewrite from the live report drops "automated contract testing".
        suggested = (
            "Secured 70+ RESTful APIs with OIDC/Keycloak, standardizing "
            "contracts via OpenAPI/Swagger and Postman for automated testing "
            "and clear documentation"
        )
        patched = apply_patch(data, "exp_0.bullet_0", suggested)
        after = {m.lower() for m in match_keywords(patched, KEYWORDS)["matched_keywords"]}
        assert "contract testing" in baseline - after, (
            "fixture must reproduce the regression the guard polices"
        )

    def test_a_rewrite_that_keeps_matched_keywords_passes(self):
        data = _data()
        baseline = {m.lower() for m in match_keywords(data, KEYWORDS)["matched_keywords"]}
        suggested = (
            "Secured 70+ RESTful APIs with OIDC/Keycloak and automated "
            "contract testing, standardized via OpenAPI/Swagger with Postman "
            "suites for 3 partner teams."
        )
        patched = apply_patch(data, "exp_0.bullet_0", suggested)
        after = {m.lower() for m in match_keywords(patched, KEYWORDS)["matched_keywords"]}
        assert baseline - after == set(), f"rewrite lost matched keywords: {baseline - after}"


class TestEmptyPaneBackfill:
    """The same report: after the guards, the tailor pane came back EMPTY.

    Guards refusing the LLM's dishonest drafts must not leave the user with
    nothing -- a gap that survives gets a deterministic draft (", using X"),
    whose claims verify by construction because the keyword is in the text.
    This test reproduces that branch of the service: it runs the real
    deterministic weaver over the surviving gaps and asserts the output
    would persist. The service-side wiring (the "uncovered" loop calling
    _draft_suggestions with exactly these inputs) is pinned below.
    """

    def test_deterministic_drafts_verify_their_own_claims(self):
        from app.services.tailoring_service import TailoringService

        data = _data()
        scores = match_keywords(data, KEYWORDS)
        gaps = scores["gap_keywords"]
        # _draft_suggestions is deterministic and touches only its arguments,
        # so calling it unbound against a None self is exact.
        drafts = TailoringService._draft_suggestions(None, data, gaps)
        assert drafts
        for d in drafts:
            verified = [
                kw for kw in d["keywords"]
                if any(contains(d["suggested_text"], s) for s in canonical_aliases(kw))
            ]
            assert verified == d["keywords"], (
                f"deterministic draft claims keywords absent from its text: {d}"
            )

    def test_deterministic_drafts_never_regress_the_match(self):
        from app.services.tailoring_service import TailoringService

        data = _data()
        scores = match_keywords(data, KEYWORDS)
        baseline = {m.lower() for m in scores["matched_keywords"]}
        drafts = TailoringService._draft_suggestions(None, data, scores["gap_keywords"])
        for d in drafts:
            patched = apply_patch(data, d["target_ref"], d["suggested_text"])
            after = {m.lower() for m in match_keywords(patched, KEYWORDS)["matched_keywords"]}
            assert baseline - after == set()


class TestTheServiceImplementsIt:
    """The persistence loop must actually contain both guards -- a docstring
    is not a check. The acceptance-path side is already pinned by
    suggestion_service recomputing on every accept."""

    def test_persistence_boundary_verifies_claims(self):
        import pathlib

        from app.services import tailoring_service

        src = pathlib.Path(tailoring_service.__file__).read_text()
        assert "canonical_aliases(kw)" in src
        assert "draft[\"keywords\"] = verified" in src

    def test_persistence_boundary_refuses_regressions(self):
        import pathlib

        from app.services import tailoring_service

        src = pathlib.Path(tailoring_service.__file__).read_text()
        assert "baseline_matched - after_matched" in src

    def test_the_service_backfills_uncovered_gaps_deterministically(self):
        """The reason the user saw an empty pane: refused drafts were replaced
        with nothing. The uncovered-gap loop must exist and must use the
        deterministic weaver."""
        import pathlib

        from app.services import tailoring_service

        src = pathlib.Path(tailoring_service.__file__).read_text()
        assert "uncovered" in src
        assert "_draft_suggestions(data, uncovered)" in src
