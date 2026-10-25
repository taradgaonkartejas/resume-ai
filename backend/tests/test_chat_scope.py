"""Chat edits must land in the section the user named -- or in no section.

Reported 2026-10-24 from the running app:

```
you   In experience section I want to want to shorten all bullet
      including tailored keyword
ai    Resume edited / Summary            <- WRONG SECTION
      "Owned full Stack Engineer with 2+ years..."
```

Two stacked defects produced that card:

  A. `iter_bullets` yields the summary as a pseudo-bullet and the rule
     fallback picked the LONGEST candidate -- the summary paragraph always
     is -- so a bullet request mangled the summary.

  B. A bulk rewrite ("shorten ALL bullets including tailored keywords") was
     silently "done" with a one-line mechanical hack, instead of being
     refused as writer/tailor work.

The fallback now excludes the summary from the bullet pool, honours an
explicit section name, and refuses bulk/keyword rewrites without touching
anything.
"""

from __future__ import annotations

import pytest

from tests.conftest import new_resume

SAMPLE = {
    "contact": {
        "name": "Tejas Shinde",
        "headline": "Engineer",
        "email": "t@x.io",
        "phone": "+91 90000 00000",
        "location": "Pune",
        "links": [],
    },
    "summary": {
        # Much longer than any bullet: this is what made it the fallback's
        # favourite "bullet" before the fix.
        "text": "Full Stack Engineer with 2+ years of experience building "
        "web applications across the stack, owning design, delivery and "
        "operations of production systems end to end."
    },
    "experience": [
        {
            "company": "Acme",
            "role": "Engineer",
            "dates": "2023 - Present",
            "bullets": [
                "Built the deployment pipeline and kept it running",
                "Cut build times from 20 minutes to 6 minutes.",
            ],
        }
    ],
    "projects": [
        {
            "name": "Resume Portal",
            "bullets": [
                "Shipped a resume parser used by students at my college",
            ],
        }
    ],
    "education": [],
    "skills": [{"label": "Languages", "items": ["Python", "TypeScript"]}],
}

REPORTED = (
    "In experience section I want to want to shorten all bullet "
    "including tailored keyword"
)


@pytest.fixture
def resume_id(client, priya):
    return new_resume(client, priya, "Scope", dict(SAMPLE))


def _send(client, user_id, resume_id, text):
    r = client.post(
        f"/api/resumes/{resume_id}/chat",
        json={"content": text},
        headers={"X-User-Id": user_id},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def _summary_text(client, user_id, resume_id):
    r = client.get(f"/api/resumes/{resume_id}", headers={"X-User-Id": user_id})
    assert r.status_code == 200, r.text
    return r.json()["structured_data"]["summary"]["text"]


# ------------------------------------------------------------------ cause B


def test_the_reported_message_edits_nothing(client, priya, resume_id):
    before = _summary_text(client, priya, resume_id)

    out = _send(client, priya, resume_id, REPORTED)

    assert out["suggestion"] is None, (
        f"bulk keyword rewrite must not produce a one-line suggestion: "
        f"{out['suggestion']}"
    )
    reply = out["message"]["content"].lower()
    assert "did not change" in reply
    # And it must say WHERE the work belongs, not leave the user guessing.
    assert "experience" in reply
    assert _summary_text(client, priya, resume_id) == before


def test_bulk_or_keyword_asks_are_refused_without_edits(
    client, priya, resume_id
):
    for message in (
        "shorten all the bullets in my experience section",
        "add keywords to every bullet",
        "rewrite each bullet to match the jd keyword",
    ):
        out = _send(client, priya, resume_id, message)
        assert out["suggestion"] is None, message
        assert "did not change" in out["message"]["content"].lower(), message


# ------------------------------------------------------------------ cause A


def test_an_unscoped_bullet_ask_never_lands_on_the_summary(
    client, priya, resume_id
):
    """The quick action 'Tighten my longest bullet' must pick a real bullet.

    With SAMPLE, the summary paragraph is longer than every bullet -- the
    exact setup that rewrote the summary before the fix.
    """
    out = _send(client, priya, resume_id, "Tighten my longest bullet")
    sugg = out["suggestion"]
    assert sugg is not None
    assert sugg["target_ref"] != "summary.text"
    assert sugg["target_ref"].startswith(("exp_", "prj_"))
    assert sugg["placement"] != "Summary"
    assert sugg["section"] in ("experience", "projects")


def test_an_experience_scoped_ask_picks_an_experience_bullet(
    client, priya, resume_id
):
    out = _send(
        client, priya, resume_id, "improve the longest bullet in my experience"
    )
    sugg = out["suggestion"]
    assert sugg is not None
    assert sugg["target_ref"].startswith("exp_")
    assert sugg["section"] == "experience"
    assert "Acme" in sugg["placement"]
    assert "experience" in out["message"]["content"].lower()


def test_a_project_scoped_ask_picks_a_project_bullet(client, priya, resume_id):
    out = _send(client, priya, resume_id, "make my project bullet stronger")
    sugg = out["suggestion"]
    assert sugg is not None
    assert sugg["target_ref"].startswith("prj_")
    assert sugg["section"] == "projects"
    assert "Resume Portal" in sugg["placement"]


def test_a_scoped_ask_for_an_empty_section_says_so_and_edits_nothing(
    client, priya
):
    resume_id = new_resume(
        client, priya, "No projects", {**SAMPLE, "projects": []}
    )
    out = _send(client, priya, resume_id, "improve the bullets in my projects")
    assert out["suggestion"] is None
    assert "projects" in out["message"]["content"].lower()
