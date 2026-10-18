"""Document-level Accept All / Decline All.

Both halves ride the EXISTING version cursor. "Decline Changes" after the
edits are applied means revert, and revert is undo -- a second history for
the document-level buttons would be two mechanisms disagreeing about what
the resume used to be.
"""

import uuid

from app.models import Suggestion


def _data():
    return {
        "contact": {"name": "A", "email": "a@b.c", "phone": "1", "location": "X", "links": []},
        "summary": {"text": "Platform engineer who ships."},
        "experience": [
            {"company": "Acme", "role": "Senior SRE", "dates": "2022-2024",
             "bullets": ["Ran the deploy pipeline", "Handled on-call"]},
            {"company": "Globex", "role": "SRE", "dates": "2020-2022",
             "bullets": ["Built monitoring"]},
        ],
        "projects": [],
        "education": [],
        "skills": [{"label": "Platform", "items": ["Go"]}],
    }


def _seed_resume(client, user_id):
    headers = {"X-User-Id": user_id}
    made = client.post("/api/resumes", json={"title": "Bulk"}, headers=headers).json()
    client.put(
        f"/api/resumes/{made['id']}/data",
        json={"structured_data": _data()},
        headers=headers,
    )
    return made["id"]


def _add(client, resume_id, target_ref, original, suggested, **kw):
    db = client._session_factory()
    row = Suggestion(
        resume_id=uuid.UUID(resume_id), session_id=kw.pop("session_id", None),
        origin=kw.pop("origin", "tailor"), section="experience",
        target_ref=target_ref, placement=kw.pop("placement", ""),
        original_text=original, suggested_text=suggested,
        keywords=[], reasoning="", status=kw.pop("status", "pending"), **kw,
    )
    db.add(row)
    db.commit()
    rid = str(row.id)
    db.close()
    return rid


# --------------------------------------------------------------- accept all
def test_accept_all_applies_every_pending_suggestion(client, priya):
    headers = {"X-User-Id": priya}
    rid = _seed_resume(client, priya)
    _add(client, rid, "exp_0.bullet_0", "Ran the deploy pipeline",
         "Ran the deploy pipeline for 9 teams")
    _add(client, rid, "exp_1.bullet_0", "Built monitoring", "Built monitoring from scratch")

    resp = client.post(f"/api/resumes/{rid}/suggestions/accept-all", json={}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied_count"] == 2
    assert body["skipped_count"] == 0

    data = client.get(f"/api/resumes/{rid}", headers=headers).json()["structured_data"]
    assert data["experience"][0]["bullets"][0] == "Ran the deploy pipeline for 9 teams"
    assert data["experience"][1]["bullets"][0] == "Built monitoring from scratch"


def test_the_whole_batch_is_one_version(client, priya):
    """One click, one undo.

    Recording a version per suggestion would make the user press undo
    twelve times to escape one button.
    """
    headers = {"X-User-Id": priya}
    rid = _seed_resume(client, priya)
    before = len(client.get(f"/api/resumes/{rid}/versions", headers=headers).json())
    _add(client, rid, "exp_0.bullet_0", "Ran the deploy pipeline", "Ran it better")
    _add(client, rid, "exp_0.bullet_1", "Handled on-call", "Handled on-call rotation")
    _add(client, rid, "exp_1.bullet_0", "Built monitoring", "Built monitoring well")

    client.post(f"/api/resumes/{rid}/suggestions/accept-all", json={}, headers=headers)
    after = client.get(f"/api/resumes/{rid}/versions", headers=headers).json()
    assert len(after) == before + 1, "three suggestions must not be three versions"
    assert after[-1]["change_source"] == "accept_all"


def test_one_undo_reverts_the_whole_batch(client, priya):
    headers = {"X-User-Id": priya}
    rid = _seed_resume(client, priya)
    _add(client, rid, "exp_0.bullet_0", "Ran the deploy pipeline", "Ran it better")
    _add(client, rid, "exp_1.bullet_0", "Built monitoring", "Built monitoring well")
    client.post(f"/api/resumes/{rid}/suggestions/accept-all", json={}, headers=headers)

    client.post(f"/api/resumes/{rid}/undo", headers=headers)
    data = client.get(f"/api/resumes/{rid}", headers=headers).json()["structured_data"]
    assert data["experience"][0]["bullets"][0] == "Ran the deploy pipeline"
    assert data["experience"][1]["bullets"][0] == "Built monitoring"


def test_two_suggestions_on_one_bullet_do_not_clobber_each_other(client, priya):
    """The subtle one.

    Both were written against the same original text. After the first is
    applied the second is genuinely stale -- judging staleness against the
    snapshot we started from would apply the second on top of the first and
    destroy it.
    """
    headers = {"X-User-Id": priya}
    rid = _seed_resume(client, priya)
    _add(client, rid, "exp_0.bullet_0", "Ran the deploy pipeline", "VERSION ONE")
    _add(client, rid, "exp_0.bullet_0", "Ran the deploy pipeline", "VERSION TWO")

    body = client.post(
        f"/api/resumes/{rid}/suggestions/accept-all", json={}, headers=headers
    ).json()
    assert body["applied_count"] == 1
    assert body["skipped_count"] == 1
    assert body["skipped"][0]["reason"] == "stale"

    data = client.get(f"/api/resumes/{rid}", headers=headers).json()["structured_data"]
    assert data["experience"][0]["bullets"][0] in {"VERSION ONE", "VERSION TWO"}


def test_a_stale_suggestion_is_skipped_not_forced(client, priya):
    headers = {"X-User-Id": priya}
    rid = _seed_resume(client, priya)
    _add(client, rid, "exp_0.bullet_0", "text the resume never had", "whatever")
    _add(client, rid, "exp_1.bullet_0", "Built monitoring", "Built monitoring well")

    body = client.post(
        f"/api/resumes/{rid}/suggestions/accept-all", json={}, headers=headers
    ).json()
    assert body["applied_count"] == 1
    assert [s["reason"] for s in body["skipped"]] == ["stale"]


def test_a_question_awaiting_an_answer_is_skipped(client, priya):
    headers = {"X-User-Id": priya}
    rid = _seed_resume(client, priya)
    _add(client, rid, "exp_0.bullet_0", "Ran the deploy pipeline", "Ran it for N teams",
         status="needs_input")
    body = client.post(
        f"/api/resumes/{rid}/suggestions/accept-all", json={}, headers=headers
    ).json()
    assert body["applied_count"] == 0


def test_nothing_pending_is_a_quiet_no_op(client, priya):
    headers = {"X-User-Id": priya}
    rid = _seed_resume(client, priya)
    before = len(client.get(f"/api/resumes/{rid}/versions", headers=headers).json())
    body = client.post(
        f"/api/resumes/{rid}/suggestions/accept-all", json={}, headers=headers
    ).json()
    assert body["applied_count"] == 0
    after = len(client.get(f"/api/resumes/{rid}/versions", headers=headers).json())
    assert after == before, "an empty batch must not record a version"


def test_another_users_resume_is_not_reachable(client, priya, arjun):
    rid = _seed_resume(client, priya)
    resp = client.post(
        f"/api/resumes/{rid}/suggestions/accept-all", json={}, headers={"X-User-Id": arjun}
    )
    assert resp.status_code == 404


# ------------------------------------------------------------- decline all
def test_restore_reverts_a_whole_batch_in_one_call(client, priya):
    """Decline All = restore to the cursor from before the batch."""
    headers = {"X-User-Id": priya}
    rid = _seed_resume(client, priya)
    _add(client, rid, "exp_0.bullet_0", "Ran the deploy pipeline", "Ran it better")
    _add(client, rid, "exp_1.bullet_0", "Built monitoring", "Built monitoring well")

    body = client.post(
        f"/api/resumes/{rid}/suggestions/accept-all", json={}, headers=headers
    ).json()
    baseline = body["undo_to_version"]
    assert baseline < body["version_cursor"]

    resp = client.post(f"/api/resumes/{rid}/restore/{baseline}", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["structured_data"]
    assert data["experience"][0]["bullets"][0] == "Ran the deploy pipeline"
    assert data["experience"][1]["bullets"][0] == "Built monitoring"


def test_restore_walks_back_more_than_one_step(client, priya):
    """Several separate accepts, then one Decline All over all of them."""
    headers = {"X-User-Id": priya}
    rid = _seed_resume(client, priya)
    baseline = client.get(f"/api/resumes/{rid}", headers=headers).json()["version_cursor"]

    for ref, orig, new in [
        ("exp_0.bullet_0", "Ran the deploy pipeline", "Ran it better"),
        ("exp_0.bullet_1", "Handled on-call", "Handled on-call rotation"),
        ("exp_1.bullet_0", "Built monitoring", "Built monitoring well"),
    ]:
        sid = _add(client, rid, ref, orig, new)
        client.patch(f"/api/suggestions/{sid}", json={"action": "accept"}, headers=headers)

    now = client.get(f"/api/resumes/{rid}", headers=headers).json()["version_cursor"]
    assert now == baseline + 3

    resp = client.post(f"/api/resumes/{rid}/restore/{baseline}", headers=headers)
    assert resp.status_code == 200
    first = resp.json()["structured_data"]["experience"][0]["bullets"][0]
    assert first == "Ran the deploy pipeline"


def test_restore_does_not_start_a_second_history(client, priya):
    """It moves the cursor; it must not append a version.

    A revert that records a new version would mean undo and Decline All
    walk two different ladders.
    """
    headers = {"X-User-Id": priya}
    rid = _seed_resume(client, priya)
    _add(client, rid, "exp_0.bullet_0", "Ran the deploy pipeline", "Ran it better")
    body = client.post(
        f"/api/resumes/{rid}/suggestions/accept-all", json={}, headers=headers
    ).json()
    count = len(client.get(f"/api/resumes/{rid}/versions", headers=headers).json())

    client.post(f"/api/resumes/{rid}/restore/{body['undo_to_version']}", headers=headers)
    assert len(client.get(f"/api/resumes/{rid}/versions", headers=headers).json()) == count


def test_redo_still_walks_forward_after_a_restore(client, priya):
    headers = {"X-User-Id": priya}
    rid = _seed_resume(client, priya)
    _add(client, rid, "exp_0.bullet_0", "Ran the deploy pipeline", "Ran it better")
    body = client.post(
        f"/api/resumes/{rid}/suggestions/accept-all", json={}, headers=headers
    ).json()
    client.post(f"/api/resumes/{rid}/restore/{body['undo_to_version']}", headers=headers)

    resp = client.post(f"/api/resumes/{rid}/redo", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["structured_data"]["experience"][0]["bullets"][0] == "Ran it better"


def test_restoring_to_the_current_cursor_is_a_no_op(client, priya):
    """So the UI can offer Decline All without first checking."""
    headers = {"X-User-Id": priya}
    rid = _seed_resume(client, priya)
    cursor = client.get(f"/api/resumes/{rid}", headers=headers).json()["version_cursor"]
    assert client.post(f"/api/resumes/{rid}/restore/{cursor}", headers=headers).status_code == 200


def test_restoring_to_a_nonexistent_version_is_refused(client, priya):
    headers = {"X-User-Id": priya}
    rid = _seed_resume(client, priya)
    assert client.post(f"/api/resumes/{rid}/restore/999", headers=headers).status_code == 422
    assert client.post(f"/api/resumes/{rid}/restore/0", headers=headers).status_code == 422


def test_restore_is_scoped_to_the_owner(client, priya, arjun):
    rid = _seed_resume(client, priya)
    assert client.post(
        f"/api/resumes/{rid}/restore/1", headers={"X-User-Id": arjun}
    ).status_code == 404
