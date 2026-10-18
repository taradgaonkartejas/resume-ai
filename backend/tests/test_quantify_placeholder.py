"""The `[__]` metric placeholder.

`summary.no_metric` and `experience.unquantified_bullets` are cleared only by
a number, and the writer must never invent one. Rather than refuse, it
restructures the line so a figure fits and leaves a blank the candidate
fills in -- which asserts nothing and so cannot fabricate anything.

These tests pin the two properties that make that safe (exactly one
placeholder, no new digits) and the guard that stops a resume leaving the
building with the blank still in it.
"""

import pytest

from app.services import placeholders as ph
from app.services.heuristics import score_resume


# ------------------------------------------------------------ the sentinel
def test_the_placeholder_is_what_every_layer_expects():
    """Writer, guards and exporter must agree on one literal."""
    assert ph.PLACEHOLDER == "[__]"


def test_both_metric_findings_use_the_quantify_path():
    assert ph.QUANTIFY_FINDINGS == {
        "summary.no_metric",
        "experience.unquantified_bullets",
    }


# --------------------------------------------------------------- new_digits
@pytest.mark.parametrize(
    "original,draft,expected",
    [
        ("Improved performance", "Improved performance by [__]%", []),
        # Existing figures survive untouched.
        ("Led 40 services", "Led 40 services, cutting cost [__]%", []),
        # A figure the line never had.
        ("Improved performance", "Improved performance by 40%", ["40"]),
        # Multiset: reusing an existing number a SECOND time invents one.
        ("Led 40 services", "Led 40 services across 40 regions", ["40"]),
        # Decimals are one number, not two.
        ("Cut latency 1.5s", "Cut latency 1.5s further", []),
        ("Cut latency", "Cut latency 1.5s", ["1.5"]),
    ],
)
def test_new_digits_compares_against_the_line(original, draft, expected):
    assert ph.new_digits(original, draft) == expected


def test_new_digits_is_stricter_than_the_whole_resume_check():
    """A number lifted from ANOTHER bullet is still fabricated here.

    `unverifiable_numbers` compares a draft against the entire resume, so a
    figure that appears anywhere passes. For this role's bullet that is not
    good enough: "40" belonging to a different job does not make "40" true
    of this one.
    """
    assert ph.new_digits("Maintained the deploy pipeline", "Maintained it, 40% faster") == ["40"]


# -------------------------------------------------------------- check_draft
def test_a_draft_without_the_placeholder_is_refused():
    complaint = ph.check_draft("Improved performance", "Greatly improved performance")
    assert complaint and "no [__] placeholder" in complaint


def test_a_draft_with_several_placeholders_is_refused():
    """One blank is a prompt; three is a puzzle."""
    complaint = ph.check_draft("Improved x", "Improved [__] x by [__]% in [__] weeks")
    assert complaint and "3 [__] placeholders" in complaint


def test_a_draft_that_invents_a_figure_is_refused():
    complaint = ph.check_draft("Improved performance", "Improved performance 40% by [__]x")
    assert complaint and "40" in complaint


def test_a_well_formed_draft_passes():
    assert ph.check_draft(
        "Improved processing performance for point-cloud datasets",
        "Improved processing performance for point-cloud datasets by [__]%",
    ) is None


def test_an_existing_figure_may_be_kept_alongside_the_placeholder():
    """The fix must not cost the resume a number it already had.

    The example changed when `check_draft` gained the new-content-word rule.
    It used to end "...Kubernetes, cutting deploy time [__]%", which the
    stricter rule refuses -- correctly. The original line never claims deploy
    time was cut, so that draft asserts a BENEFIT and leaves only its size
    blank, which is structurally the same move as the "serving [__] users"
    failure reported from a real resume.

    The intent of this test is that the existing `40` survives the rewrite,
    and that is unchanged and still asserted. Only the vehicle moved.
    """
    assert ph.check_draft(
        "Led migration of 40 services to Kubernetes",
        "Led migration of 40 services to Kubernetes in [__] weeks",
    ) is None


def test_a_draft_may_not_add_an_unclaimed_benefit():
    """The boundary the test above used to sit on the wrong side of.

    Kept explicitly so the decision is visible rather than implied by an
    edited example: adding a plausible upside is still adding a claim. This
    is deliberately strict, and it is affordable because the complaint is
    retryable -- `rewrite_for_finding` feeds it back and returns attempt two
    regardless, so the cost of being wrong here is one model call, not a
    dead end for the user.
    """
    complaint = ph.check_draft(
        "Led migration of 40 services to Kubernetes",
        "Led migration of 40 services to Kubernetes, cutting deploy time [__]%",
    )
    assert complaint is not None
    assert "deploy" in complaint or "cutting" in complaint


# ----------------------------------------------------------- finding wiring
def _resume_without_metrics():
    return {
        "contact": {
            "name": "Jane", "email": "j@x.com", "phone": "555",
            "location": "Pune", "links": ["https://github.com/j"],
        },
        "summary": {"text": " ".join(["Engineer building reliable platforms"] * 6)},
        "experience": [{
            "company": "Acme", "role": "Platform Engineer", "dates": "2020-2024",
            "bullets": [
                "Improved processing performance for point-cloud datasets",
                "Maintained the continuous deployment pipeline",
            ],
        }],
        "projects": [],
        "education": [{"school": "S", "degree": "BSc", "dates": "2016"}],
        "skills": [{"label": "P", "items": ["Python", "Go"]}],
    }


def test_the_two_metric_findings_now_offer_an_action():
    """They carried action="" precisely because the fix was a number.

    The placeholder mechanism is what makes an action honest here, so the
    absence of one is no longer correct -- but it must be `quantify`, not
    `rewrite`: a plain rewrite would promise to supply the figure.
    """
    result = score_resume(_resume_without_metrics())
    by_id = {f["id"]: f for f in result["findings"]}

    for fid in ("summary.no_metric", "experience.unquantified_bullets"):
        assert fid in by_id, f"{fid} should fire on a resume with no numbers"
        assert by_id[fid]["action"] == "quantify", (
            f"{fid} must use the placeholder path, not a plain rewrite"
        )


def test_no_other_finding_was_given_the_quantify_action():
    """Scope is two cards. A third would be a silent widening."""
    result = score_resume(_resume_without_metrics())
    quantify = {f["id"] for f in result["findings"] if f.get("action") == "quantify"}
    assert quantify <= ph.QUANTIFY_FINDINGS


# ------------------------------------------------------------ export guard
def test_find_in_resume_reports_every_unfilled_blank():
    data = _resume_without_metrics()
    data["summary"]["text"] = "Engineer who cut build times by [__]%"
    data["experience"][0]["bullets"][1] = "Maintained the pipeline, [__] deploys a week"

    found = ph.find_in_resume(data)
    assert "summary.text" in found
    assert "exp_0.bullet_1" in found
    assert "exp_0.bullet_0" not in found


def test_find_in_resume_is_quiet_on_a_clean_resume():
    assert ph.find_in_resume(_resume_without_metrics()) == []


def test_export_refuses_a_resume_with_an_unfilled_placeholder(client, priya):
    """Worse to send than the missing metric it fixes.

    A warning would be read after the file is already downloaded and
    attached, so this refuses.
    """
    headers = {"X-User-Id": priya}
    made = client.post("/api/resumes", json={"title": "Blank"}, headers=headers).json()
    data = _resume_without_metrics()
    data["experience"][0]["bullets"][0] = "Improved performance by [__]%"
    client.put(
        f"/api/resumes/{made['id']}/data",
        json={"structured_data": data},
        headers=headers,
    )

    resp = client.get(f"/api/resumes/{made['id']}/export?format=txt", headers=headers)
    assert resp.status_code == 422, resp.text
    assert "[__]" in resp.json()["detail"]


def test_export_succeeds_once_the_blank_is_filled(client, priya):
    headers = {"X-User-Id": priya}
    made = client.post("/api/resumes", json={"title": "Filled"}, headers=headers).json()
    data = _resume_without_metrics()
    data["experience"][0]["bullets"][0] = "Improved performance by [__]%"
    client.put(
        f"/api/resumes/{made['id']}/data", json={"structured_data": data}, headers=headers
    )
    assert client.get(
        f"/api/resumes/{made['id']}/export?format=txt", headers=headers
    ).status_code == 422

    data["experience"][0]["bullets"][0] = "Improved performance by 35%"
    client.put(
        f"/api/resumes/{made['id']}/data", json={"structured_data": data}, headers=headers
    )
    assert client.get(
        f"/api/resumes/{made['id']}/export?format=txt", headers=headers
    ).status_code == 200


def test_a_cached_clean_export_does_not_let_a_placeholder_through(client, priya):
    """Export clean, add a placeholder, export again -- still refused.

    Note on what this does NOT prove: moving the guard to after the cache
    lookup leaves this passing, because the cache key contains
    version_cursor and editing the resume always misses. The ordering is
    therefore not load-bearing today, and an earlier version of this
    docstring claimed a stale-key risk that does not exist. What the test
    does cover is the realistic sequence, including the fact that a prior
    successful export grants no exemption.
    """
    headers = {"X-User-Id": priya}
    made = client.post("/api/resumes", json={"title": "Cache"}, headers=headers).json()
    data = _resume_without_metrics()
    client.put(
        f"/api/resumes/{made['id']}/data", json={"structured_data": data}, headers=headers
    )
    assert client.get(
        f"/api/resumes/{made['id']}/export?format=txt", headers=headers
    ).status_code == 200

    data["summary"]["text"] = "Engineer who cut build times by [__]%"
    client.put(
        f"/api/resumes/{made['id']}/data", json={"structured_data": data}, headers=headers
    )
    assert client.get(
        f"/api/resumes/{made['id']}/export?format=txt", headers=headers
    ).status_code == 422
