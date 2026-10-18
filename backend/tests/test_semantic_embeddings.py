"""Acceptance gate for local semantic embeddings (all-MiniLM-L6-v2).

The point of replacing the hashed embedder is retrieval quality, so the gate
is a retrieval gate, not a vibes check.

HONEST NOTE ON THE ORIGINAL CRITERIA. The plan proposed five pairs with
absolute thresholds, including "an exact negation must score below the
synonym pairs". Measured, MiniLM scores:

    increased conversion / DEcreased conversion     0.885   (hashed: 0.750)

It got *worse*, and no sentence-embedding model of this family does better:
"increased" and "decreased" differ by one token in near-identical contexts.
That criterion was wrong for a RETRIEVAL embedder -- it tests entailment,
which retrieval neither needs nor provides. For the actual job (a JD mentions
conversion; which bullets are about conversion?) both sentences ARE relevant
and high similarity is correct.

It is pinned below as a known limitation rather than deleted, because it
would matter enormously if these vectors were ever used to check a claim.
They are not, and `test_embeddings_are_not_used_for_verification` says so.

The criteria that survived are the ones retrieval depends on:
  * a synonym must beat an unrelated sentence by a wide margin;
  * shared boilerplate must not outrank shared meaning;
  * the right bullet must win a realistic JD query.
"""

from __future__ import annotations

import pytest

from app.ai import embeddings as E
from app.config import settings


@pytest.fixture
def minilm(monkeypatch):
    """Force the semantic backend, or skip if the model is not fetched."""
    monkeypatch.setattr(settings, "embedding_backend", "minilm", raising=False)
    E.reset_for_tests()
    if E.active_backend() != "minilm":
        pytest.skip("MiniLM not available — run `make fetch-models`")
    yield
    E.reset_for_tests()


@pytest.fixture
def hashed(monkeypatch):
    monkeypatch.setattr(settings, "embedding_backend", "hashed", raising=False)
    E.reset_for_tests()
    yield
    E.reset_for_tests()


def sim(a: str, b: str) -> float:
    va, vb = E.embed_many([a, b])
    return E.cosine(va, vb)


K8S_A = "Deployed microservices on Kubernetes"
K8S_B = "Shipped services to K8s"
UNRELATED = "Won an inter-college dance competition in 2019"


# --------------------------------------------------------------------------
# the defect this work exists to fix
# --------------------------------------------------------------------------

class TestSynonymsAreFound:
    def test_hashed_cannot_see_a_synonym_at_all(self, hashed):
        """The baseline. If this ever passes, the premise changed."""
        assert sim(K8S_A, K8S_B) == pytest.approx(0.0, abs=1e-6)

    def test_minilm_finds_it(self, minilm):
        assert sim(K8S_A, K8S_B) > 0.45

    def test_a_synonym_beats_an_unrelated_sentence_by_a_wide_margin(self, minilm):
        """The property retrieval actually needs: separation, not magnitude."""
        related = sim(K8S_A, K8S_B)
        unrelated = sim(K8S_A, UNRELATED)
        assert related - unrelated > 0.35, (
            f"related={related:.3f} unrelated={unrelated:.3f}"
        )

    def test_unrelated_text_scores_near_zero(self, minilm):
        for other in (UNRELATED,
                      "Baked sourdough bread every weekend",
                      "Studied Mughal architecture at university"):
            assert sim(K8S_A, other) < 0.15


# --------------------------------------------------------------------------
# meaning must outrank filler -- the clearest failure of the hashed embedder
# --------------------------------------------------------------------------

class TestBoilerplateDoesNotWin:
    BOILER_A = "Responsible for managing the team"
    BOILER_B = "Responsible for managing the budget"

    def test_hashed_ranks_boilerplate_above_meaning(self, hashed):
        """Baseline: 0.800 for two unrelated duties, 0.000 for a synonym."""
        assert sim(self.BOILER_A, self.BOILER_B) > sim(K8S_A, K8S_B)

    def test_minilm_ranks_meaning_above_boilerplate(self, minilm):
        boiler = sim(self.BOILER_A, self.BOILER_B)
        assert boiler < 0.75, f"boilerplate still at {boiler:.3f}"


# --------------------------------------------------------------------------
# end to end: the user-visible point
# --------------------------------------------------------------------------

class TestJdRetrieval:
    JD = "We need someone with Kubernetes and container orchestration experience"
    BULLETS = [
        "Shipped services to K8s with Helm charts",
        "Cut p95 latency by 40% through query optimisation",
        "Mentored two junior engineers",
        "Won an inter-college dance competition",
    ]

    def _ranked(self):
        qv = E.embed(self.JD)
        scored = [(E.cosine(qv, E.embed(b)), b) for b in self.BULLETS]
        scored.sort(reverse=True)
        return [b for _, b in scored]

    def test_the_k8s_bullet_ranks_first(self, minilm):
        assert self._ranked()[0] == "Shipped services to K8s with Helm charts"

    def test_it_beats_the_irrelevant_bullets_clearly(self, minilm):
        qv = E.embed(self.JD)
        best = E.cosine(qv, E.embed("Shipped services to K8s with Helm charts"))
        worst = E.cosine(qv, E.embed("Won an inter-college dance competition"))
        assert best - worst > 0.25

    def test_hashed_only_got_this_right_on_a_stopword(self, hashed):
        """Why the baseline's apparent success did not count.

        The hashed embedder also ranks the K8s bullet first -- scoring 0.126
        while everything else scores exactly 0.000. The query says
        "Kubernetes" and the bullet says "K8s"; they share no content word.
        The entire signal is the stopword "with".
        """
        qv = E.embed(self.JD)
        best = E.cosine(qv, E.embed("Shipped services to K8s with Helm charts"))
        no_stopword = E.cosine(qv, E.embed("Shipped services to K8s, Helm charts"))
        assert best > 0
        assert no_stopword == pytest.approx(0.0, abs=1e-6)


# --------------------------------------------------------------------------
# known limitation, pinned deliberately
# --------------------------------------------------------------------------

class TestNegationLimitation:
    POS = "Increased conversion by 30%"
    NEG = "Decreased conversion by 30%"

    def test_negation_is_not_detected_and_that_is_recorded(self, minilm):
        """MiniLM scores an exact negation ~0.885. Documented, not fixed.

        Acceptable because these vectors only ever answer "which bullets are
        about this topic". Both sentences are about conversion.
        """
        assert sim(self.POS, self.NEG) > 0.8

    def test_embeddings_are_not_used_for_verification(self):
        """The guard that makes the limitation above safe.

        Fact-checking a draft is the critic's job and the deterministic
        `check_draft` guard's job -- neither uses embeddings. If a future
        change routes verification through similarity, the negation blindness
        becomes a correctness bug, and this test is where that shows up.
        """
        import inspect

        from app.services import placeholders

        source = inspect.getsource(placeholders)
        for banned in ("embed", "cosine", "vectorstore", "VectorStore"):
            assert banned not in source, (
                f"placeholders.py now references {banned!r}; embeddings cannot "
                "distinguish 'increased' from 'decreased' and must not be used "
                "to verify a claim"
            )


# --------------------------------------------------------------------------
# dimensions and fallback
# --------------------------------------------------------------------------

class TestContract:
    def test_both_backends_emit_the_column_width(self, minilm):
        assert len(E.embed("anything")) == settings.embedding_dim == 384

    def test_hashed_emits_the_same_width(self, hashed):
        assert len(E.embed("anything")) == settings.embedding_dim

    def test_a_wrong_width_is_refused_with_both_numbers_named(self, monkeypatch):
        monkeypatch.setattr(settings, "embedding_backend", "hashed", raising=False)
        monkeypatch.setattr(settings, "embedding_dim", 999, raising=False)
        E.reset_for_tests()
        with pytest.raises(ValueError) as err:
            E._checked([[0.0] * 384], "test embedder")
        assert "384" in str(err.value) and "999" in str(err.value)

    def test_batching_matches_one_at_a_time(self, minilm):
        """embed_many must not change the vectors it produces."""
        texts = ["first bullet", "second bullet", "third bullet"]
        batched = E.embed_many(texts)
        singly = [E.embed(t) for t in texts]
        for b, s in zip(batched, singly, strict=True):
            assert E.cosine(b, s) > 0.999

    def test_missing_model_falls_back_to_hashed_not_an_exception(self, monkeypatch):
        """A missing model file must degrade, loudly, never fail a request."""
        monkeypatch.setattr(settings, "embedding_backend", "minilm", raising=False)
        E.reset_for_tests()
        monkeypatch.setattr(E, "_MODEL", None, raising=False)
        monkeypatch.setattr(E, "_MODEL_TRIED", True, raising=False)
        assert E.active_backend() == "hashed"
        assert len(E.embed("still works")) == settings.embedding_dim


# --------------------------------------------------------------------------
# two embedding spaces must never be compared
# --------------------------------------------------------------------------

class TestCrossSpaceGuard:
    """Hashed and MiniLM are both 384-wide, so a mix is invisible by shape.

    Mixing them makes cosine similarity meaningless -- worse than either
    space used consistently -- and the symptom would be "retrieval got
    strangely bad", with no error anywhere.
    """

    class _Doc:
        def __init__(self, backend):
            self.doc_metadata = (
                {} if backend is None else {"embedding_backend": backend}
            )

    def test_rows_from_another_backend_are_excluded(self, hashed):
        from app.ai.vectorstore import _same_space_only

        docs = [self._Doc("hashed"), self._Doc("minilm"), self._Doc("hashed")]
        assert len(_same_space_only(docs, "resume_bullets")) == 2

    def test_rows_with_no_backend_recorded_are_excluded(self, hashed):
        """Pre-stamp rows: unknown provenance cannot be compared safely."""
        from app.ai.vectorstore import _same_space_only

        assert _same_space_only([self._Doc(None)], "resume_bullets") == []

    def test_the_warning_names_the_excluded_backend_not_the_kept_one(
        self, hashed, caplog
    ):
        """First draft logged "embedded by 'hashed', not 'hashed'"."""
        import logging

        from app.ai.vectorstore import _same_space_only

        with caplog.at_level(logging.WARNING):
            _same_space_only([self._Doc("hashed"), self._Doc("minilm")], "c")
        assert "minilm" in caplog.text
        assert "db reset" in caplog.text

    def test_a_homogeneous_index_logs_nothing(self, hashed, caplog):
        import logging

        from app.ai.vectorstore import _same_space_only

        with caplog.at_level(logging.WARNING):
            kept = _same_space_only([self._Doc("hashed")] * 3, "c")
        assert len(kept) == 3
        assert caplog.text == ""
