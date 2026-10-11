"""The ATS task must never send personal data to its provider.

`MODEL_ATS` is routed to codecraft, a reseller that names no upstream
provider -- so we cannot know whose servers its payloads land on. That was
accepted for this one task, and ONLY this task, on a specific factual basis:
after section relabelling was deleted on 2026-10-11, the entire user message
for the ATS agent is

    Skills written only as an acronym:
    PMP, CKA

No name, no phone, no employer, no dates. It is a list of certification
tokens, no more sensitive than typing "PMP" into a search engine.

That basis is a property of one line in `review_ats`, and nothing stops a
future change from enriching the payload "for context" -- which is exactly
what the deleted relabel half used to do: it sent section titles and sample
entries like `Senior Engineer - Globex - 2018-2020`, i.e. employment
history. If that happens again the privacy argument evaporates silently and
nobody re-reads the comment in config.py.

So the argument is pinned here as an executable invariant. If this test
fails, either revert the payload change or move `model_ats` to a provider
that will tell you who it is.
"""

from __future__ import annotations

import pytest

from app.ai import agents

# A resume whose every personal field is a distinctive, greppable token.
_PII = {
    "name": "Zalthorix Quendrimble",
    "phone": "+91 98765 43210",
    "email": "zalthorix@example.com",
    "location": "Koregaon Park, Pune",
    "company": "Vrexibular Dynamics",
    "role": "Principal Payments Engineer",
    "date": "Mar 2019 - Aug 2024",
    "bullet": "Cut settlement latency from 22 minutes to 9 minutes.",
    "school": "Quibbleton Institute of Technology",
    "extra_title": "Leadership & Achievements",
    "extra_primary": "Chaired the Glorbnax steering committee",
}

RESUME = {
    "contact": {
        "name": _PII["name"], "email": _PII["email"],
        "phone": _PII["phone"], "location": _PII["location"],
    },
    "summary": {"text": f"Engineer at {_PII['company']} building payments."},
    "experience": [{
        "company": _PII["company"], "role": _PII["role"],
        "dates": _PII["date"], "bullets": [_PII["bullet"]],
    }],
    "education": [{"school": _PII["school"], "degree": "BE", "dates": "2015 - 2019"}],
    # Acronyms are what SHOULD be sent; PMP/CKA are not personal data.
    "skills": [{"label": "", "items": ["Python", "PMP", "CKA"]}],
    "extras": [{
        "kind": "custom",
        "title": _PII["extra_title"],
        "entries": [{"primary": _PII["extra_primary"], "secondary": _PII["company"],
                     "date": _PII["date"], "detail": ""}],
    }],
}


@pytest.fixture
def sent(monkeypatch):
    """Capture exactly what would go over the wire, without a network call."""
    seen: dict = {}

    def spy(**kwargs):
        seen["system"] = kwargs.get("system", "")
        seen["user"] = kwargs.get("user", "")
        raise RuntimeError("captured")       # agent falls back to rules

    monkeypatch.setattr(agents.llm, "is_configured", lambda: True)
    monkeypatch.setattr(agents.llm, "structured", spy)
    agents.review_ats(RESUME)
    assert seen, "the agent never called the model -- the gate did not open"
    return seen


@pytest.mark.parametrize("label,value", sorted(_PII.items()))
def test_no_personal_field_reaches_the_provider(sent, label, value):
    blob = f"{sent['system']}\n{sent['user']}"
    assert value not in blob, (
        f"the ATS payload now contains the user's {label!r}. "
        "model_ats is routed to a provider that does not disclose its "
        "upstream; that was justified solely by this payload being "
        "acronyms-only. Revert, or move model_ats to a named vendor."
    )


def test_the_payload_is_only_the_acronyms(sent):
    assert sent["user"] == "Skills written only as an acronym:\nPMP, CKA"


def test_the_spy_would_notice_a_leak():
    """Guard the guard -- prove the assertion can actually fire."""
    leaked = f"Custom section {_PII['extra_title']!r} with {_PII['company']}"
    assert _PII["company"] in leaked
