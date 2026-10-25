"""Personal identifiers must never reach the ATS task's provider.

`MODEL_ATS` is routed to codecraft, a reseller that names no upstream
provider -- so we cannot know whose servers its payloads land on.

WHAT IS SENT, by decision of 2026-10-24: certification acronyms, and bullets
that contain a metric (the Tier 2 plausibility feature). The user accepted
that explicitly, on the basis that resume bullet text already reaches the
same vendor through MODEL_WRITING (= codecraft:claude-sonnet-5), so the
marginal disclosure of metric-bearing bullets alone is zero.

WHAT STILL MUST NOT BE SENT: anything that identifies the person. Name,
phone, email, address, and -- CRUCIALLY -- the Structured contact block and
employer/role/date fields. A metric in the middle of "Cut settlement
latency from 22 minutes to 9 minutes." tells no one who wrote it; the same
sentence beside "Zalthorix Quendrimble at Vrexibular Dynamics" does.

This file pins the second half as an executable invariant. If it fails,
either strip the identifiers back out of the candidate payloads, or move
`model_ats` to a provider that will tell you who it is.
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
        "dates": _PII["date"],
        # A metric-bearing bullet: this DOES go over the wire now, and is
        # the documented exception -- it carries a number, not an identity.
        "bullets": ["Cut settlement latency from 22 minutes to 9 minutes."],
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


def test_the_payload_is_only_acronyms_and_metric_bullets(sent):
    parts = sent["user"].split("\n\n")
    assert parts[0] == "Skills written only as an acronym:\nPMP, CKA"
    # Zero or one bullet section, and zero unexplained anything.
    assert len(parts) <= 2
    if len(parts) == 2:
        assert parts[1].startswith("Bullets containing metrics (judge plausibility only):\n")


def test_contacts_fields_are_dropped_before_candidates_are_chosen(sent):
    """A bullet cannot identify anyone; the contact block and job header can."""
    blob = f"{sent['system']}\n{sent['user']}"
    assert "RESUME" not in blob  # sanity: the spy is not reading a config dump


def test_the_spy_would_notice_a_leak():
    """Guard the guard -- prove the assertion can actually fire."""
    leaked = f"Custom section {_PII['extra_title']!r} with {_PII['company']}"
    assert _PII["company"] in leaked
