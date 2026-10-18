"""Every suggestion must reach the client priced in resume points.

The Tailor tab could only ever answer "does this look like the job ad".
"Is my resume getting better or worse" was unanswerable there, so a user
could drive keyword match to 100% with no idea what it did to their score.

`score_delta` is the same arithmetic the Analysis tab prints, from the same
rule engine, so a `+2` in either tab means the same thing.
"""

from __future__ import annotations

import uuid as _uuid

from app.models import JobDescription, Suggestion, TailoringSession
from app.services.heuristics import score_resume
from tests.conftest import new_resume

SAMPLE = {
    "contact": {"name": "Sam Rivera", "headline": "Engineer", "email": "s@x.io",
                "phone": "+1 555 0100", "location": "Pune", "links": []},
    "summary": {"text": "Platform engineer with six years building and running "
                        "cloud infrastructure for high-traffic services."},
    "experience": [{
        "company": "Acme", "role": "Engineer", "dates": "2020 - Present",
        "bullets": [
            "Responsible for the deployment pipeline and its upkeep",
            "Cut build times from 20 minutes to 6 minutes.",
        ],
    }],
    "projects": [], "education": [],
    "skills": [{"label": "Languages", "items": ["Python", "SQL"]}],
}


def _session_with_suggestion(client, user_id, suggested: str, ref: str):
    resume_id = new_resume(client, user_id, "Priced", dict(SAMPLE))
    db = client._session_factory()
    jd = JobDescription(title="SRE", content="kubernetes terraform")
    db.add(jd)
    db.flush()
    sess = TailoringSession(
        resume_id=_uuid.UUID(str(resume_id)), job_description_id=jd.id,
        match_percent=0.0, baseline_percent=0.0, matched_keywords=[],
        gap_keywords=["kubernetes"], thread_id="t", graph_state="done",
    )
    db.add(sess)
    db.flush()
    db.add(Suggestion(
        session_id=sess.id, resume_id=_uuid.UUID(str(resume_id)),
        origin="tailor", section="experience", target_ref=ref,
        placement="Engineer - Acme",
        original_text=SAMPLE["experience"][0]["bullets"][0],
        suggested_text=suggested, edited_text="", keywords=["kubernetes"],
        reasoning="r", status="pending", grounded=True, critic_notes="",
        revisions=0,
    ))
    db.commit()
    sid = str(sess.id)
    db.close()
    return resume_id, sid


def _buckets(client, user_id, resume_id, session_id):
    r = client.get(f"/api/resumes/{resume_id}/tailor/{session_id}/suggestions",
                   headers={"X-User-Id": user_id})
    assert r.status_code == 200, r.text
    return r.json()


def test_score_delta_is_on_the_wire(client, priya):
    resume_id, sid = _session_with_suggestion(
        client, priya, "Owned the deployment pipeline end to end.",
        "exp_0.bullet_0",
    )
    body = _buckets(client, priya, resume_id, sid)
    assert body["active"], "fixture produced no active suggestion"
    assert "score_delta" in body["active"][0]
    assert isinstance(body["active"][0]["score_delta"], int)


def test_a_real_improvement_prices_above_zero(client, priya):
    """A weak-verb bullet replaced by a strong one must show a gain.

    If this ever reads 0 the badge is decorative, which is worse than absent.
    """
    resume_id, sid = _session_with_suggestion(
        client, priya,
        "Rebuilt the deployment pipeline, cutting release failures by 35 percent.",
        "exp_0.bullet_0",
    )
    delta = _buckets(client, priya, resume_id, sid)["active"][0]["score_delta"]

    before = score_resume(SAMPLE)["overall_score"]
    patched = {**SAMPLE, "experience": [{
        **SAMPLE["experience"][0],
        "bullets": [
            "Rebuilt the deployment pipeline, cutting release failures by 35 percent.",
            SAMPLE["experience"][0]["bullets"][1],
        ],
    }]}
    assert delta == score_resume(patched)["overall_score"] - before
    assert delta > 0, "a weak verb replaced by a quantified one should gain points"


def test_an_identical_rewrite_prices_zero(client, priya):
    resume_id, sid = _session_with_suggestion(
        client, priya, SAMPLE["experience"][0]["bullets"][0], "exp_0.bullet_0",
    )
    assert _buckets(client, priya, resume_id, sid)["active"][0]["score_delta"] == 0


def test_pricing_does_not_leak_across_users(client, priya, arjun):
    """`_attach_score_delta` loads the resume to score it. That load must stay
    ownership-scoped or it becomes a read primitive for someone else's data."""
    resume_id, sid = _session_with_suggestion(
        client, priya, "Owned the deployment pipeline.", "exp_0.bullet_0",
    )
    r = client.get(f"/api/resumes/{resume_id}/tailor/{sid}/suggestions",
                   headers={"X-User-Id": arjun})
    assert r.status_code == 404


# --------------------------------------------------------------- staleness
def test_a_suggestion_is_not_stale_until_the_text_moves(client, priya):
    resume_id, sid = _session_with_suggestion(
        client, priya, "Owned the deployment pipeline end to end.",
        "exp_0.bullet_0",
    )
    assert _buckets(client, priya, resume_id, sid)["active"][0]["is_stale"] is False


def test_editing_the_target_marks_it_stale_BEFORE_accept(client, priya):
    """The whole point of the flag.

    `act()` already refuses this with a 409, but only once the user has read
    the card, compared the bullets and clicked. Work the Analysis tab first
    and every tailor card is a trap. The card must know in advance.
    """
    resume_id, sid = _session_with_suggestion(
        client, priya, "Owned the deployment pipeline end to end.",
        "exp_0.bullet_0",
    )
    # simulate the user fixing that same bullet on the Analysis side
    r = client.put(
        f"/api/resumes/{resume_id}/data",
        headers={"X-User-Id": priya},
        json={"structured_data": {**SAMPLE, "experience": [{
            **SAMPLE["experience"][0],
            "bullets": ["Rebuilt the deployment pipeline from scratch.",
                        SAMPLE["experience"][0]["bullets"][1]],
        }]}},
    )
    assert r.status_code == 200, r.text

    card = _buckets(client, priya, resume_id, sid)["active"][0]
    assert card["is_stale"] is True, (
        "the Analysis tab changed this bullet and the Tailor card did not notice"
    )


def test_the_stale_card_would_indeed_409_on_accept(client, priya):
    """Guard the guard: the flag must predict the real failure, not approximate it.

    A flag that says 'stale' where accept would actually succeed is noise;
    one that says 'fine' where accept 409s is the bug we started with.
    """
    resume_id, sid = _session_with_suggestion(
        client, priya, "Owned the deployment pipeline end to end.",
        "exp_0.bullet_0",
    )
    client.put(
        f"/api/resumes/{resume_id}/data",
        headers={"X-User-Id": priya},
        json={"structured_data": {**SAMPLE, "experience": [{
            **SAMPLE["experience"][0],
            "bullets": ["Different text entirely.",
                        SAMPLE["experience"][0]["bullets"][1]],
        }]}},
    )
    card = _buckets(client, priya, resume_id, sid)["active"][0]
    assert card["is_stale"] is True

    r = client.patch(f"/api/suggestions/{card['id']}",
                     headers={"X-User-Id": priya},
                     json={"action": "accept"})
    assert r.status_code == 409, (
        f"flag and reality disagree: is_stale=True but accept returned {r.status_code}"
    )


def test_an_accepted_suggestion_is_never_stale(client, priya):
    """It is SUPPOSED to differ from original_text -- that is what accepting did."""
    resume_id, sid = _session_with_suggestion(
        client, priya, "Owned the deployment pipeline end to end.",
        "exp_0.bullet_0",
    )
    card = _buckets(client, priya, resume_id, sid)["active"][0]
    r = client.patch(f"/api/suggestions/{card['id']}",
                     headers={"X-User-Id": priya}, json={"action": "accept"})
    assert r.status_code == 200, r.text

    body = _buckets(client, priya, resume_id, sid)
    assert body["matched"], "accepted suggestion did not move to matched"
    assert body["matched"][0]["is_stale"] is False
