"""Phase 1 (docs/ai-module-plan.md): circuit breaker + versioned cache.

No live GEMINI_API_KEY anywhere in this file. `_generate_json` and the cache
functions are monkeypatched at the `medstock_shared.ai.core` names they were
imported under (same pattern the rest of this service's tests use for
`app.main.*`) -- one MagicMock genai client, one in-memory fake cache, no
network, no Postgres.
"""

from unittest.mock import MagicMock

import pytest
from medstock_shared.ai import core as ai_core
from medstock_shared.ai.breaker import CircuitBreaker, CircuitBreakerConfig
from medstock_shared.ai.core import AIError, ask_ai
from medstock_shared.ai_tasks import TASKS, AITask
from medstock_shared.auth import Principal

# conftest.py's autouse `_gemini_configured` fixture already sets a fake
# GEMINI_API_KEY for every test in this directory; nothing below ever calls
# the real Gemini API regardless -- `_get_client`/`_generate_json` are always
# monkeypatched before use.


# ---------------------------------------------------------------------------
# CircuitBreaker in isolation -- no ask_ai, no mocking, just the state machine.
# ---------------------------------------------------------------------------


def test_starts_closed_and_allows_calls():
    b = CircuitBreaker()
    assert b.state == "CLOSED"
    assert b.allow() is True


def test_opens_after_consecutive_failures_and_short_circuits():
    b = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3))
    for _ in range(3):
        b.record(False)
    assert b.state == "OPEN"
    assert b.allow() is False


def test_success_resets_the_failure_count():
    b = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3))
    b.record(False)
    b.record(False)
    b.record(True)  # not yet at threshold, and this clears the streak
    b.record(False)
    b.record(False)
    assert b.state == "CLOSED"  # would be OPEN if the streak hadn't reset


class _FakeClock:
    """Deterministic stand-in for time.monotonic() -- the breaker's recovery
    window is time-based, and real sleeps have no place in a unit test."""

    def __init__(self) -> None:
        self._t = 0.0

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


def test_recovers_through_half_open_on_a_successful_probe(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr("medstock_shared.ai.breaker.time.monotonic", clock)
    b = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, recovery_seconds=30))
    b.record(False)
    assert b.state == "OPEN"
    assert b.allow() is False  # recovery window hasn't elapsed yet

    clock.advance(30)
    assert b.allow() is True  # HALF_OPEN probe granted
    b.record(True)
    assert b.state == "CLOSED"


def test_half_open_failure_reopens_without_hitting_threshold_again(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr("medstock_shared.ai.breaker.time.monotonic", clock)
    b = CircuitBreaker(CircuitBreakerConfig(failure_threshold=5, recovery_seconds=30))
    for _ in range(5):
        b.record(False)
    assert b.state == "OPEN"

    clock.advance(30)
    assert b.allow() is True  # probe granted
    b.record(False)  # probe failed -- one failure reopens, not five
    assert b.state == "OPEN"

    clock.advance(1)  # nowhere near another 30s recovery window
    assert b.allow() is False


# ---------------------------------------------------------------------------
# ask_ai() wiring: breaker + versioned cache, with the network and DB faked.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_breaker(monkeypatch):
    """The real breaker is a module-level singleton shared by every task; give
    each test a fresh one so failures in one test can't open it for another."""
    monkeypatch.setattr(ai_core, "_breaker", CircuitBreaker(CircuitBreakerConfig(failure_threshold=3)))


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """tenacity's own recommended mock point (tenacity/nap.py) -- 429 retries
    use wait_exponential and would otherwise really sleep."""
    monkeypatch.setattr("tenacity.nap.sleep", lambda seconds: None)


@pytest.fixture
def fake_cache(monkeypatch):
    """In-memory stand-in for the ai_cache table, keyed exactly like the
    widened DB constraint: (type, prompt_version, dedupe_key). Also no-ops
    the audit write, so no test in this file touches a real Postgres --
    tests that need to inspect what got audited ask for `audit_calls`
    instead, which overrides this no-op."""
    store: dict[tuple[str, str, str], dict] = {}

    def fake_get(task_name, prompt_version, key):
        return store.get((task_name, prompt_version, key))

    def fake_put(task_name, prompt_version, model_name, key, result):
        store[(task_name, prompt_version, key)] = result

    monkeypatch.setattr(ai_core, "cache_get", fake_get)
    monkeypatch.setattr(ai_core, "cache_put", fake_put)
    monkeypatch.setattr(ai_core, "write_audit", lambda **kwargs: None)
    return store


@pytest.fixture
def audit_calls(fake_cache, monkeypatch):
    """Every write_audit(**kwargs) call ask_ai made, in order. Depends on
    fake_cache so its no-op patch is applied first and this one wins."""
    calls: list[dict] = []
    monkeypatch.setattr(ai_core, "write_audit", lambda **kwargs: calls.append(kwargs))
    return calls


@pytest.fixture
def fake_task():
    task = AITask(name="_test_task", owner="test", prompt="hello {x}", prompt_version="v1")
    TASKS["_test_task"] = task
    yield task
    TASKS.pop("_test_task", None)


def test_bumping_prompt_version_is_a_cache_miss(monkeypatch, fake_cache, fake_task):
    calls: list[str] = []
    monkeypatch.setattr(ai_core, "_generate_json", lambda prompt, timeout_seconds=None: calls.append(prompt) or {"ok": True})

    payload = {"x": "same question"}
    ask_ai("_test_task", payload)
    ask_ai("_test_task", payload)
    assert len(calls) == 1  # second call replayed from cache, no "Gemini" call

    TASKS["_test_task"] = AITask(
        name="_test_task", owner="test", prompt="hello {x}", prompt_version="v2"
    )
    ask_ai("_test_task", payload)
    assert len(calls) == 2  # edited prompt -> different version -> real miss


def test_429_retries_and_never_trips_the_breaker(monkeypatch, fake_cache, fake_task):
    class _RateLimited(Exception):
        status_code = 429

    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = _RateLimited("slow down")
    monkeypatch.setattr(ai_core, "_get_client", lambda: fake_client)

    with pytest.raises(AIError):
        ask_ai("_test_task", {"x": "1"})

    assert fake_client.models.generate_content.call_count == 3  # tenacity's stop_after_attempt
    assert ai_core._breaker.state == "CLOSED"  # 429 is not an outage signal


def test_5xx_trips_the_breaker_and_then_short_circuits(monkeypatch, fake_cache, fake_task):
    class _ServerError(Exception):
        status_code = 503

    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = _ServerError("down")
    monkeypatch.setattr(ai_core, "_get_client", lambda: fake_client)

    for i in range(3):  # failure_threshold from the _reset_breaker fixture
        with pytest.raises(AIError):
            ask_ai("_test_task", {"x": str(i)})
    assert ai_core._breaker.state == "OPEN"

    calls_before = fake_client.models.generate_content.call_count
    with pytest.raises(AIError, match="circuit breaker open"):
        ask_ai("_test_task", {"x": "one-more"})
    # short-circuited: no network call at all for this one
    assert fake_client.models.generate_content.call_count == calls_before


def test_cache_hit_bypasses_an_open_breaker(monkeypatch, fake_cache, fake_task):
    key = ai_core.dedupe_key("_test_task", {"x": "cached"})
    fake_cache[("_test_task", "v1", key)] = {"ok": True}
    monkeypatch.setattr(ai_core._breaker, "allow", lambda: False)

    result = ask_ai("_test_task", {"x": "cached"})
    assert result == {"ok": True}


# ---------------------------------------------------------------------------
# Provenance (docs/ai-module-plan.md Phase 2): one ai_audit_log row per call,
# attributed to the caller's Principal, joinable to the ai_cache row that
# answered via dedupe_key.
# ---------------------------------------------------------------------------

PHARMACIST = Principal("user-1", "hospital-1", "pharmacist")


def test_live_call_writes_one_audit_row_attributed_to_the_caller(monkeypatch, audit_calls, fake_task):
    monkeypatch.setattr(ai_core, "_generate_json", lambda prompt, timeout_seconds=None: {"ok": True})

    ask_ai("_test_task", {"x": "1"}, principal=PHARMACIST, request_id="req-1")

    assert len(audit_calls) == 1
    row = audit_calls[0]
    assert row["hospital_id"] == "hospital-1"
    assert row["actor_id"] == "user-1"
    assert row["request_id"] == "req-1"
    assert row["task_type"] == "_test_task"
    assert row["outcome"] == "live"
    assert row["dedupe_key"] == ai_core.dedupe_key("_test_task", {"x": "1"})


def test_repeat_call_is_a_cache_hit_audit_row_and_no_second_gemini_call(
    monkeypatch, audit_calls, fake_task
):
    calls: list[str] = []
    monkeypatch.setattr(
        ai_core, "_generate_json", lambda prompt, timeout_seconds=None: calls.append(prompt) or {"ok": True}
    )

    payload = {"x": "same"}
    ask_ai("_test_task", payload, principal=PHARMACIST)
    ask_ai("_test_task", payload, principal=PHARMACIST)

    assert len(calls) == 1  # second call never touched "Gemini"
    assert [row["outcome"] for row in audit_calls] == ["live", "cache_hit"]
    # both rows point at the same cache row -- that's the provenance join
    assert audit_calls[0]["dedupe_key"] == audit_calls[1]["dedupe_key"]


def test_no_principal_is_attributed_to_system_ingest_with_no_hospital(
    monkeypatch, audit_calls, fake_task
):
    monkeypatch.setattr(ai_core, "_generate_json", lambda prompt, timeout_seconds=None: {"ok": True})

    ask_ai("_test_task", {"x": "offline"})  # ingest's CronJob call shape: no principal

    assert audit_calls[0]["actor_id"] == "system:ingest"
    assert audit_calls[0]["hospital_id"] is None


def test_breaker_open_still_writes_an_audit_row_before_raising(monkeypatch, audit_calls, fake_task):
    monkeypatch.setattr(ai_core._breaker, "allow", lambda: False)

    with pytest.raises(AIError):
        ask_ai("_test_task", {"x": "1"}, principal=PHARMACIST)

    assert audit_calls[0]["outcome"] == "breaker_open"
