"""The Redis LLM response cache.

Redis sat in docker-compose, environment.yaml and config.py from the start
and was read by zero lines of application code. This is its one job.

The tests that matter here are not the hit-rate ones. They are:

  * the writer is NEVER cached (temperature 0.4 + a retry UI depend on it),
  * editing a prompt invalidates every entry,
  * a failure is never cached,
  * and the whole thing is inert when Redis is unreachable.
"""

from __future__ import annotations

import pytest

from app.ai import cache


@pytest.fixture(autouse=True)
def _clean_cache_state():
    cache.reset_for_tests()
    yield
    cache.reset_for_tests()


@pytest.fixture
def fake_redis(monkeypatch):
    """A real redis client API backed by an in-process fake."""
    fakeredis = pytest.importorskip("fakeredis")
    server = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(cache.settings, "llm_cache_enabled", True, raising=False)
    monkeypatch.setattr(cache, "_CLIENT", server, raising=False)
    monkeypatch.setattr(cache, "_CLIENT_READY", True, raising=False)
    return server


# --------------------------------------------------------------------------
# policy: what may be cached at all
# --------------------------------------------------------------------------

class TestPolicy:
    def test_the_writer_is_never_cacheable(self, monkeypatch):
        """Temperature 0.4 and 'try again' only mean something if it varies."""
        monkeypatch.setattr(cache.settings, "llm_cache_enabled", True, raising=False)
        assert cache.cacheable("writing", 0.4) is False
        # ...not even if someone later calls it at a low temperature.
        assert cache.cacheable("writing", 0.0) is False

    def test_chat_is_never_cacheable(self, monkeypatch):
        monkeypatch.setattr(cache.settings, "llm_cache_enabled", True, raising=False)
        assert cache.cacheable("chat", 0.2) is False

    @pytest.mark.parametrize("task", ["extraction", "jd_analysis", "scoring", "ats"])
    def test_deterministic_tasks_are_cacheable(self, task, monkeypatch):
        monkeypatch.setattr(cache.settings, "llm_cache_enabled", True, raising=False)
        assert cache.cacheable(task, 0.2) is True

    def test_a_creative_temperature_overrides_the_allowlist(self, monkeypatch):
        """A caller asking for variation must get variation."""
        monkeypatch.setattr(cache.settings, "llm_cache_enabled", True, raising=False)
        assert cache.cacheable("scoring", 0.9) is False

    def test_nothing_is_cacheable_while_the_feature_is_off(self, monkeypatch):
        monkeypatch.setattr(cache.settings, "llm_cache_enabled", False, raising=False)
        for task in cache.CACHEABLE_TASKS:
            assert cache.cacheable(task, 0.0) is False

    def test_the_writer_is_absent_from_the_allowlist_itself(self):
        """Belt and braces: not merely filtered, not on the list."""
        assert "writing" not in cache.CACHEABLE_TASKS
        assert "chat" not in cache.CACHEABLE_TASKS


# --------------------------------------------------------------------------
# key construction
# --------------------------------------------------------------------------

class TestKey:
    def _key(self, **over):
        base = dict(task="scoring", model="groq:m", system="sys",
                    user="usr", temperature=0.2)
        base.update(over)
        return cache.make_key(**base)

    def test_identical_inputs_give_identical_keys(self):
        assert self._key() == self._key()

    @pytest.mark.parametrize("field,value", [
        ("task", "ats"), ("model", "groq:other"), ("system", "sys2"),
        ("user", "usr2"), ("temperature", 0.1), ("schema", "S:[a]"),
    ])
    def test_every_input_participates(self, field, value):
        assert self._key() != self._key(**{field: value})

    def test_editing_a_prompt_invalidates_everything(self, monkeypatch):
        """The sharpest edge: change WRITER rule 6, get yesterday's answers."""
        before = self._key()
        cache.reset_for_tests()
        monkeypatch.setattr(cache, "_PROMPTS_FINGERPRINT", "deadbeefdeadbeef",
                            raising=False)
        assert self._key() != before

    def test_the_fingerprint_is_derived_from_the_real_file(self):
        """Not a constant someone must remember to bump."""
        fp = cache._prompts_fingerprint()
        assert fp != "unknown" and len(fp) == 16


# --------------------------------------------------------------------------
# round trip
# --------------------------------------------------------------------------

class TestRoundTrip:
    def test_put_then_get(self, fake_redis):
        key = cache.make_key(task="scoring", model="m", system="s",
                             user="u", temperature=0.2)
        cache.put(key, {"content": "hello", "model": "m", "status": "ok"})
        assert cache.get(key)["content"] == "hello"

    def test_a_miss_is_none(self, fake_redis):
        assert cache.get("resumeai:llm:nothing-here") is None

    def test_entries_expire(self, fake_redis):
        key = cache.make_key(task="ats", model="m", system="s",
                             user="u", temperature=0.2)
        cache.put(key, {"content": "x"})
        assert 0 < fake_redis.ttl(key) <= cache.TTL_SECONDS

    def test_a_corrupt_entry_is_a_miss_not_a_crash(self, fake_redis):
        fake_redis.set("resumeai:llm:bad", "{not json")
        assert cache.get("resumeai:llm:bad") is None


# --------------------------------------------------------------------------
# availability -- the non-negotiable one
# --------------------------------------------------------------------------

class TestRedisDown:
    def test_an_unreachable_redis_is_a_miss_and_never_raises(self, monkeypatch):
        monkeypatch.setattr(cache.settings, "llm_cache_enabled", True, raising=False)
        # A port nothing listens on, with a 0.25s connect timeout.
        monkeypatch.setattr(cache.settings, "redis_url",
                            "redis://127.0.0.1:6399/0", raising=False)
        cache.reset_for_tests()
        assert cache.get("resumeai:llm:anything") is None
        cache.put("resumeai:llm:anything", {"content": "x"})   # must not raise
        assert cache.get("resumeai:llm:anything") is None

    def test_a_broken_client_does_not_propagate(self, monkeypatch):
        class Exploding:
            def get(self, *a, **k):
                raise RuntimeError("connection reset")

            def setex(self, *a, **k):
                raise RuntimeError("connection reset")

        monkeypatch.setattr(cache.settings, "llm_cache_enabled", True, raising=False)
        monkeypatch.setattr(cache, "_CLIENT", Exploding(), raising=False)
        monkeypatch.setattr(cache, "_CLIENT_READY", True, raising=False)
        assert cache.get("k") is None
        cache.put("k", {"content": "x"})

    def test_disabled_never_touches_the_network(self, monkeypatch):
        monkeypatch.setattr(cache.settings, "llm_cache_enabled", False, raising=False)
        cache.reset_for_tests()

        def explode(*a, **k):  # pragma: no cover
            raise AssertionError("tried to connect while disabled")

        monkeypatch.setattr("redis.Redis.from_url", explode, raising=False)
        assert cache._client() is None


# --------------------------------------------------------------------------
# integration with the call path
# --------------------------------------------------------------------------

class TestCallPath:
    def test_a_second_identical_call_hits_and_is_marked_cached(
        self, fake_redis, monkeypatch
    ):
        from app.ai import llm

        calls = {"n": 0}

        class FakeResponse:
            content = "the answer"
            response_metadata: dict = {}
            usage_metadata: dict = {}

        class FakeClient:
            def invoke(self, _messages):
                calls["n"] += 1
                return FakeResponse()

        monkeypatch.setattr(llm, "is_configured", lambda: True)
        monkeypatch.setattr(llm, "resolve_chain", lambda task: ["groq:test-model"])
        monkeypatch.setattr(llm, "_client", lambda *a, **k: FakeClient())

        first = llm.complete("scoring", "system", "user", temperature=0.2)
        second = llm.complete("scoring", "system", "user", temperature=0.2)

        assert calls["n"] == 1, "the second call reached the provider"
        assert second.content == first.content == "the answer"
        assert second.trace[0].get("cached") is True
        assert first.trace[0].get("cached") is not True

    def test_the_writer_calls_the_provider_every_time(self, fake_redis, monkeypatch):
        from app.ai import llm

        calls = {"n": 0}

        class FakeResponse:
            content = "draft"
            response_metadata: dict = {}
            usage_metadata: dict = {}

        class FakeClient:
            def invoke(self, _messages):
                calls["n"] += 1
                return FakeResponse()

        monkeypatch.setattr(llm, "is_configured", lambda: True)
        monkeypatch.setattr(llm, "resolve_chain", lambda task: ["codecraft:w"])
        monkeypatch.setattr(llm, "_client", lambda *a, **k: FakeClient())

        llm.complete("writing", "system", "user", temperature=0.4)
        llm.complete("writing", "system", "user", temperature=0.4)
        assert calls["n"] == 2, "the writer was served from cache"

    def test_a_failure_is_not_cached(self, fake_redis, monkeypatch):
        from app.ai import llm

        calls = {"n": 0}

        class Failing:
            def invoke(self, _messages):
                calls["n"] += 1
                raise RuntimeError("Request timed out")

        monkeypatch.setattr(llm, "is_configured", lambda: True)
        monkeypatch.setattr(llm, "resolve_chain", lambda task: ["groq:test-model"])
        monkeypatch.setattr(llm, "_client", lambda *a, **k: Failing())

        for _ in range(2):
            with pytest.raises(llm.LLMUnavailable):
                llm.complete("scoring", "system", "user", temperature=0.2)
        assert calls["n"] == 2, "a transient failure became permanent"

    def test_a_different_user_prompt_misses(self, fake_redis, monkeypatch):
        from app.ai import llm

        calls = {"n": 0}

        class FakeResponse:
            content = "a"
            response_metadata: dict = {}
            usage_metadata: dict = {}

        class FakeClient:
            def invoke(self, _messages):
                calls["n"] += 1
                return FakeResponse()

        monkeypatch.setattr(llm, "is_configured", lambda: True)
        monkeypatch.setattr(llm, "resolve_chain", lambda task: ["groq:test-model"])
        monkeypatch.setattr(llm, "_client", lambda *a, **k: FakeClient())

        llm.complete("scoring", "system", "resume A", temperature=0.2)
        llm.complete("scoring", "system", "resume B", temperature=0.2)
        assert calls["n"] == 2
