"""Every LLM schema must be expressible in strict json_schema mode.

Groq (and OpenAI) require `additionalProperties: false` on every object in a
`response_format` schema. A `dict[str, str]` field cannot satisfy that by
construction -- an open-ended map means the keys are unknown at build time,
which is the exact opposite of what the rule demands.

This shipped and failed in production:

    invalid JSON schema for response_format: 'ScoringCommentary':
    /properties/category_notes: `additionalProperties:false` must be set
    on every object

It failed on EVERY model in the chain, so there was no fallback. The scoring
agent degraded to rule-engine commentary on every single analysis, and the
only evidence was a line in the server log -- the API still returned 201 and
the UI still rendered. A silent, total, permanent degradation.

The test is deliberately at the schema layer rather than the transport
layer: it needs no key, no network and no LangChain internals, so it runs in
the default hermetic suite where it will actually be seen.
"""

from __future__ import annotations

import pytest

from app.ai import schemas

# Everything handed to llm.structured() as `schema=`.
LLM_SCHEMAS = [
    schemas.ScoringCommentary,
    schemas.AtsReview,
    schemas.JDAnalysis,
    schemas.CriticVerdict,
    schemas.CriticBatch,
    schemas.ChatReply,
    schemas.RevisionDraft,
    schemas.WriterOutput,
    schemas.DraftSuggestion,
]


def _open_maps(node, path="$") -> list[str]:
    """Paths of objects whose additionalProperties is itself a schema.

    `additionalProperties` absent or False is fine. A *schema* there means
    "any key, values of this type" — an open map, which strict mode rejects.
    """
    bad: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "object" and isinstance(
            node.get("additionalProperties"), dict
        ):
            bad.append(path)
        for key, value in node.items():
            bad += _open_maps(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            bad += _open_maps(value, f"{path}[{i}]")
    return bad


@pytest.mark.parametrize("model", LLM_SCHEMAS, ids=lambda m: m.__name__)
def test_no_open_ended_maps(model):
    offenders = _open_maps(model.model_json_schema())
    assert not offenders, (
        f"{model.__name__} has an open-ended map at {offenders}. "
        "Strict json_schema mode requires additionalProperties:false on every "
        "object, which dict[str, X] can never satisfy. Name the fields."
    )


def test_the_check_catches_the_bug_that_shipped():
    """A test that cannot fail is worth nothing — prove this one can."""
    from pydantic import BaseModel, Field

    class Regressed(BaseModel):
        headline: str = ""
        category_notes: dict[str, str] = Field(default_factory=dict)

    assert _open_maps(Regressed.model_json_schema()) == [
        "$/properties/category_notes"
    ]


def test_scoring_commentary_carries_all_six_categories():
    """The fix must not quietly drop data the UI reads.

    Six since 2026-10-25: the ATS table added skills and projects as scored
    categories, and contact/format stay in the commentary so the advisory
    gates can still be explained.
    """
    notes = schemas.CategoryNotes()
    assert set(notes.model_dump()) == {
        "contact", "summary", "skills", "experience", "projects", "format",
    }
