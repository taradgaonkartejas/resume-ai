"""Models that must never receive resume data.

The exclusion follows the DATA, not the brand: Google's free tier trains on
submissions from outside the EEA/UK/CH, and these payloads are real names,
phone numbers and employment history. It therefore applies through a reseller
too -- a Gemini reached via CodeCraft is the same disclosure as a direct call.

This is a guard rather than a preference because the failure was live. A real
`backend/.env` carried `MODEL_DEFAULT=gemini-3.5-flash-lite:free`, and
`model_default` is `FALLBACK_CHAIN[0]`, which made it the FIRST fallback for
every task. The only thing preventing the call was an empty UnoRouter key --
luck, not a control, and luck that ends the moment someone pastes a key in.
"""

import pytest

from app.ai import llm


@pytest.fixture()
def all_providers_configured(monkeypatch):
    """Pretend every provider has a key.

    resolve_chain drops models whose provider has no key, and the test suite
    runs with none -- so without this the chain empties for an unrelated
    reason and the exclusion assertion never gets a chance to fail.
    """
    class _Stub:
        configured = True

    monkeypatch.setattr(llm, "provider_for", lambda _m: _Stub())
    monkeypatch.setattr(llm.settings, "free_models_only", False)


@pytest.mark.parametrize(
    "model",
    [
        "gemini-3.5-flash-lite:free",
        "gemini-3.6-flash",
        "gemini-3.7-flash",
        "codecraft:gemini-3.1-pro",
        "gemma-2-2b",
        "codecraft:gemma-2-2b",
        "GEMINI-3.6-FLASH",
    ],
)
def test_excluded_models_are_recognised(model):
    assert llm.is_excluded(model), f"{model} must never be callable"


@pytest.mark.parametrize(
    "model",
    [
        "groq:openai/gpt-oss-120b",
        "groq:openai/gpt-oss-20b",
        "codecraft:claude-sonnet-5",
        "codecraft:deepseek-v4-pro-0813",
        "nvidia:meta/llama-3.3-70b-instruct",
    ],
)
def test_models_in_use_are_not_excluded(model):
    """Guard the guard: an over-broad pattern would disable the whole app."""
    assert not llm.is_excluded(model)


def test_a_reseller_prefix_does_not_smuggle_one_back_in():
    """The brand is matched on the bare name, not only the qualified id.

    A future provider prefix must not become a way back in.
    """
    assert llm.is_excluded("someprovider:gemini-9-ultra")


def test_the_excluded_default_is_stripped_from_every_chain(
    monkeypatch, all_providers_configured
):
    """The exact misconfiguration found in a real .env.

    model_default is FALLBACK_CHAIN[0], so a Gemini default made it the FIRST
    fallback for every task. Patch the chain directly rather than reloading
    the module: a reload rebinds module globals that other test modules have
    already imported by value, which contaminates the rest of the run.
    """
    monkeypatch.setattr(
        llm, "FALLBACK_CHAIN", ["gemini-3.5-flash-lite:free", "groq:openai/gpt-oss-120b"]
    )

    for task in ("writing", "critique", "ats", "scoring", "chat"):
        chain = llm.resolve_chain(task)
        leaked = [m for m in chain if llm.is_excluded(m)]
        assert not leaked, f"{task} chain would call {leaked}"


def test_the_fixture_above_would_leak_without_the_guard(
    monkeypatch, all_providers_configured
):
    """Proves the previous test grades something.

    Without _drop_excluded the same chain keeps the excluded id, so the
    assertion is not passing for some unrelated reason.
    """
    monkeypatch.setattr(
        llm, "FALLBACK_CHAIN", ["gemini-3.5-flash-lite:free", "groq:openai/gpt-oss-120b"]
    )
    monkeypatch.setattr(llm, "_drop_excluded", lambda chain, task: chain)

    chain = llm.resolve_chain("writing")
    assert any(llm.is_excluded(m) for m in chain), (
        "fixture no longer reaches the guard, so the test above proves nothing"
    )


def test_the_warning_names_the_model_and_the_file_to_edit(caplog):
    """A silent drop is its own bug.

    The user cannot fix a .env they are never told is wrong, and the symptom
    otherwise is only that a task quietly runs on its second choice.
    """
    llm._EXCLUDED_LOGGED.clear()
    try:
        with caplog.at_level("WARNING"):
            llm._drop_excluded(
                ["gemini-3.5-flash-lite:free", "groq:openai/gpt-oss-120b"], "writing"
            )
        text = caplog.text
        assert "gemini-3.5-flash-lite:free" in text
        assert ".env" in text
    finally:
        llm._EXCLUDED_LOGGED.clear()


def test_the_warning_is_logged_once_per_model(caplog):
    """It sits on a per-call path; repeating it would bury the real output."""
    llm._EXCLUDED_LOGGED.clear()
    try:
        with caplog.at_level("WARNING"):
            for _ in range(3):
                llm._drop_excluded(["gemini-3.6-flash"], "writing")
        assert caplog.text.count("gemini-3.6-flash") == 1
    finally:
        llm._EXCLUDED_LOGGED.clear()


def test_filtering_happens_before_the_api_key_check():
    """An absent key must not be what saves us.

    If the exclusion ran after the provider-key filter, the warning would
    stay invisible for exactly as long as the key was empty -- i.e. right up
    until the moment it stopped being safe.
    """
    kept = llm._drop_excluded(["gemini-3.5-flash-lite:free"], "writing")
    assert kept == [], "excluded models must be dropped regardless of key state"


# --------------------------------------------- the writer that never ran
def test_an_unconfigured_writer_does_not_deliver_a_verdict(client, priya):
    """With no provider key, "needs a fact only you have" is a lie.

    The rule engine cannot strengthen every bullet, and when it cannot the
    draft comes back equal to the original. That was read as "the writer
    answered and declined", so the user was told their resume needed a number
    they did not have -- by a writer that was never configured.

    Stays a 422, which was the right status all along: with no key, retrying
    changes nothing and the client's isFinalRefusal must keep suppressing the
    retry button. Only the stated CAUSE was wrong.
    """
    headers = {"X-User-Id": priya}
    made = client.post("/api/resumes", json={"title": "No writer"}, headers=headers).json()
    client.put(
        f"/api/resumes/{made['id']}/data",
        json={
            "structured_data": {
                "contact": {
                    "name": "Jane",
                    "email": "j@x.com",
                    "phone": "555",
                    "location": "Pune",
                    "links": ["gh"],
                },
                "summary": {"text": ""},
                "experience": [
                    {
                        "company": "Acme",
                        "role": "Platform Engineer",
                        "dates": "2020-2024",
                        "bullets": [
                            "Worked on the deployment pipeline",
                            "Helped with the monitoring stack",
                        ],
                    }
                ],
                "skills": [{"label": "P", "items": ["Python", "Terraform"]}],
                "education": [{"school": "S", "degree": "BSc", "dates": "2016"}],
                "projects": [],
            }
        },
        headers=headers,
    )

    resp = client.post(
        f"/api/resumes/{made['id']}/sections/exp_0.bullet_0/rewrite",
        json={"finding_id": "experience.weak_verbs"},
        headers=headers,
    )

    if resp.status_code == 201:
        pytest.skip("a provider key is configured; the writer actually ran")

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["reason"] == "writer_not_configured"
    detail = body["detail"].lower()
    assert "no ai writer is configured" in detail
    # The two phrasings this bug is about, both of which would be false here:
    # no model formed a view about the user's facts, and retrying is useless.
    assert "fact only you have" not in detail
    assert "try again in a minute" not in detail
