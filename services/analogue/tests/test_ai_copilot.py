"""Phase 4 (docs/ai-module-plan.md): the copilot's SSE loop, tool dispatch,
and RBAC.

No live GEMINI_API_KEY anywhere in this file. The genai async client is a
small hand-built fake (`_FakeClient` below) rather than a MagicMock, because
copilot.py drives it with `async for` -- the fake's `generate_content_stream`
is itself an async-generator method, which is what makes that work without
touching the real SDK's types.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from app.main import app
from fastapi.testclient import TestClient
from medstock_shared.ai import core as ai_core
from medstock_shared.ai.breaker import CircuitBreaker
from medstock_shared.auth import Principal, current_principal

PHARMACIST = Principal("user-1", "hospital-1", "pharmacist")


def _client(principal: Principal = PHARMACIST) -> TestClient:
    app.dependency_overrides[current_principal] = lambda: principal
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def _events(sse_text: str) -> list[tuple[str, dict]]:
    """Parse `event: x\\ndata: {...}\\n\\n` blocks into (event, data) pairs."""
    out = []
    for block in sse_text.strip().split("\n\n"):
        if not block.strip():
            continue
        lines = block.splitlines()
        event = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        out.append((event, data))
    return out


class _FakeChunk:
    """`function_calls` items become real `Part`s under `.candidates[0].content.parts`
    (not a flat `.function_calls` list) -- copilot.py reads parts, not the SDK's
    `chunk.function_calls` convenience property, because that property drops
    `thought_signature` and that omission is the bug this file guards against."""

    def __init__(self, text=None, function_calls=None):
        self.text = text
        parts = [
            SimpleNamespace(
                function_call=fc, thought_signature=getattr(fc, "thought_signature", None)
            )
            for fc in (function_calls or [])
        ]
        self.candidates = [SimpleNamespace(content=SimpleNamespace(parts=parts))]


class _FakeModels:
    """One `turns` entry per expected `generate_content_stream` call.

    `generate_content_stream` itself is a plain coroutine that *returns* an
    async generator, matching the real google-genai 2.18.0 calling
    convention (copilot.py `await`s it, then does `async for` on the
    result) -- not an async-generator function directly. Getting this wrong
    here is exactly what let the `await` bug ship past a green test suite:
    the old version of this fake matched the bug, not the SDK.
    """

    def __init__(self, turns: list[list[_FakeChunk]], calls: list, configs: list):
        self._turns = list(turns)
        self._calls = calls
        self._configs = configs

    async def generate_content_stream(self, *, model, contents, config):
        self._calls.append(list(contents))
        self._configs.append(config)
        chunks = self._turns.pop(0)

        async def _chunks():
            for chunk in chunks:
                yield chunk

        return _chunks()


class _FakeClient:
    def __init__(self, turns: list[list[_FakeChunk]]):
        self.calls: list = []
        self.configs: list = []
        self.aio = SimpleNamespace(models=_FakeModels(turns, self.calls, self.configs))


@pytest.fixture(autouse=True)
def _reset_breaker(monkeypatch):
    monkeypatch.setattr(ai_core, "_breaker", CircuitBreaker())


@pytest.fixture(autouse=True)
def _no_audit_db(monkeypatch):
    """Every test captures audit calls instead of hitting Postgres."""
    calls: list[dict] = []
    monkeypatch.setattr("app.copilot.write_audit", lambda **kwargs: calls.append(kwargs))
    return calls


@pytest.fixture
def audit_calls(_no_audit_db):
    return _no_audit_db


def test_plain_text_turn_streams_deltas_and_writes_live_audit(monkeypatch, audit_calls):
    fake = _FakeClient(turns=[[_FakeChunk(text="Hello"), _FakeChunk(text=" world")]])
    monkeypatch.setattr("app.copilot.client", lambda: fake)

    res = _client().post("/copilot/chat", json={"messages": [{"role": "user", "text": "hi"}]})
    assert res.status_code == 200
    events = _events(res.text)
    assert [e for e, _ in events] == ["delta", "delta", "done"]
    assert events[0][1]["text"] == "Hello"
    assert events[1][1]["text"] == " world"
    assert len(fake.calls) == 1  # one round -- no tool calls, no second turn

    assert audit_calls[0]["outcome"] == "live"
    assert audit_calls[0]["tools_called"] == []
    assert audit_calls[0]["actor_id"] == "user-1"
    assert audit_calls[0]["hospital_id"] == "hospital-1"


def test_tool_call_round_trip_executes_and_continues_to_a_final_answer(
    monkeypatch, audit_calls
):
    fake = _FakeClient(
        turns=[
            [_FakeChunk(function_calls=[SimpleNamespace(name="search_analogues_rxnorm", args={"rxcui": "212033"})])],
            [_FakeChunk(text="Try 105798, it's well stocked.")],
        ]
    )
    monkeypatch.setattr("app.copilot.client", lambda: fake)

    captured_args = {}

    async def fake_execute(name, args, principal):
        captured_args["name"] = name
        captured_args["args"] = args
        captured_args["principal"] = principal
        return {"items": [{"rxcui": "105798", "quantity": 80}]}

    monkeypatch.setattr("app.copilot.execute", fake_execute)

    res = _client().post(
        "/copilot/chat", json={"messages": [{"role": "user", "text": "shortage on 212033"}]}
    )
    events = _events(res.text)
    assert [e for e, _ in events] == ["tool_start", "tool_end", "delta", "done"]
    assert events[0][1] == {"name": "search_analogues_rxnorm", "args": {"rxcui": "212033"}}
    assert events[1][1] == {"name": "search_analogues_rxnorm", "ok": True}
    assert events[2][1]["text"] == "Try 105798, it's well stocked."

    assert captured_args["name"] == "search_analogues_rxnorm"
    assert captured_args["principal"] == PHARMACIST
    assert len(fake.calls) == 2  # the tool result went back for a second round

    assert audit_calls[0]["outcome"] == "live"
    assert audit_calls[0]["tools_called"] == [{"name": "search_analogues_rxnorm", "ok": True}]


def test_thought_signature_survives_the_tool_round_trip(monkeypatch, audit_calls):
    """Regression test for the bug that actually shipped: Gemini 3 requires
    `thought_signature` echoed back on any function-call part sent in a later
    turn, or the next call 400s with 'missing a thought_signature in
    functionCall parts'. `chunk.function_calls` drops it; copilot.py must
    read `.candidates[0].content.parts` instead, which keeps it."""
    fc = SimpleNamespace(name="verify_batch_cert", args={"ndc": "123"}, thought_signature=b"sig-xyz")
    fake = _FakeClient(
        turns=[[_FakeChunk(function_calls=[fc])], [_FakeChunk(text="green.")]]
    )
    monkeypatch.setattr("app.copilot.client", lambda: fake)

    async def fake_execute(name, args, principal):
        return {"status": "green"}

    monkeypatch.setattr("app.copilot.execute", fake_execute)

    res = _client().post("/copilot/chat", json={"messages": [{"role": "user", "text": "check it"}]})
    assert res.status_code == 200
    assert [e for e, _ in _events(res.text)] == ["tool_start", "tool_end", "delta", "done"]

    second_round_contents = fake.calls[1]
    model_turn = next(c for c in second_round_contents if c.role == "model")
    assert model_turn.parts[0].thought_signature == b"sig-xyz"


def test_forged_tool_call_is_denied_not_crashed(monkeypatch, audit_calls):
    """A model calling a tool name it was never declared -- unregistered, or
    one this role lacks the permission for. execute() is real here (not
    mocked): the denial has to come from the actual registry, not a stub."""
    fake = _FakeClient(
        turns=[
            [_FakeChunk(function_calls=[SimpleNamespace(name="delete_all_stock", args={})])],
            [_FakeChunk(text="I can't do that, but here's what I can help with.")],
        ]
    )
    monkeypatch.setattr("app.copilot.client", lambda: fake)

    res = _client().post("/copilot/chat", json={"messages": [{"role": "user", "text": "wipe it"}]})
    assert res.status_code == 200
    events = _events(res.text)
    assert [e for e, _ in events] == ["tool_start", "tool_end", "delta", "done"]
    assert events[1][1]["ok"] is False
    assert "delete_all_stock" in events[1][1]["error"]

    assert audit_calls[0]["tools_called"] == [
        {"name": "delete_all_stock", "ok": False, "error": events[1][1]["error"]}
    ]


def test_breaker_open_short_circuits_before_any_gemini_call(monkeypatch, audit_calls):
    fake = _FakeClient(turns=[])  # popping a turn would IndexError -- must never be reached
    monkeypatch.setattr("app.copilot.client", lambda: fake)
    monkeypatch.setattr(ai_core._breaker, "allow", lambda: False)

    res = _client().post("/copilot/chat", json={"messages": [{"role": "user", "text": "hi"}]})
    events = _events(res.text)
    assert [e for e, _ in events] == ["degraded", "done"]
    assert fake.calls == []
    assert audit_calls[0]["outcome"] == "breaker_open"


def test_gemini_stream_error_degrades_instead_of_crashing(monkeypatch, audit_calls):
    class _BoomModels:
        async def generate_content_stream(self, *, model, contents, config):
            raise RuntimeError("upstream 503")

    fake = SimpleNamespace(aio=SimpleNamespace(models=_BoomModels()))
    monkeypatch.setattr("app.copilot.client", lambda: fake)

    res = _client().post("/copilot/chat", json={"messages": [{"role": "user", "text": "hi"}]})
    assert res.status_code == 200
    events = _events(res.text)
    assert [e for e, _ in events] == ["degraded", "done"]
    assert audit_calls[0]["outcome"] == "error"


def test_stream_call_must_be_awaited_before_iterating(monkeypatch, audit_calls):
    """Regression test for the bug that actually shipped: google-genai
    2.18.0's generate_content_stream returns a coroutine, not directly an
    async iterator, despite its own `AsyncIterator[...]` return annotation
    describing the awaited value. A fake whose `generate_content_stream` is
    itself an async-generator function (the pre-fix shape every other fake
    in this file used to have) would make this pass even with the bug back
    -- this one is built the same mismatched way on purpose, so it only
    passes if copilot.py actually awaits before iterating."""

    class _WrongShapeModels:
        async def generate_content_stream(self, *, model, contents, config):
            yield _FakeChunk(text="this should never be reachable without an await")

    fake = SimpleNamespace(aio=SimpleNamespace(models=_WrongShapeModels()))
    monkeypatch.setattr("app.copilot.client", lambda: fake)

    res = _client().post("/copilot/chat", json={"messages": [{"role": "user", "text": "hi"}]})
    assert res.status_code == 200
    events = _events(res.text)
    # A missing `await` raises TypeError from `async for` itself, which is
    # exactly what should degrade the turn -- not a 500, not a hang.
    assert [e for e, _ in events] == ["degraded", "done"]
    assert audit_calls[0]["outcome"] == "error"
    assert ai_core._breaker.state == "CLOSED"  # one failure, below the trip threshold


def test_429_degrades_this_turn_but_never_trips_the_breaker(monkeypatch, audit_calls):
    """Same exemption ai/core.py's _Retryable makes for ask_ai() -- a rate
    limit is the provider asking this one call to back off, not an outage."""

    class _RateLimited(Exception):
        status_code = 429

    class _BusyModels:
        async def generate_content_stream(self, *, model, contents, config):
            raise _RateLimited("slow down")

    fake = SimpleNamespace(aio=SimpleNamespace(models=_BusyModels()))
    monkeypatch.setattr("app.copilot.client", lambda: fake)

    for _ in range(5):  # well past the 3-failure threshold this fixture's breaker uses
        res = _client().post("/copilot/chat", json={"messages": [{"role": "user", "text": "hi"}]})
        assert [e for e, _ in _events(res.text)] == ["degraded", "done"]

    assert ai_core._breaker.state == "CLOSED"
    assert all(row["outcome"] == "error" for row in audit_calls)


def test_no_gemini_key_short_circuits_without_touching_the_breaker_or_client(
    monkeypatch, audit_calls
):
    from medstock_shared.config import settings

    monkeypatch.setattr(settings, "gemini_api_key", "")
    fake_client_fn = MagicMock()
    monkeypatch.setattr("app.copilot.client", fake_client_fn)

    res = _client().post("/copilot/chat", json={"messages": [{"role": "user", "text": "hi"}]})
    events = _events(res.text)
    assert [e for e, _ in events] == ["degraded", "done"]
    assert fake_client_fn.call_count == 0
    assert audit_calls == []  # not even an audit row -- this never became a real turn


def test_chat_requires_copilot_permission():
    unknown_role = Principal("user-9", "hospital-1", "not-a-real-role")
    res = _client(unknown_role).post("/copilot/chat", json={"messages": []})
    assert res.status_code == 403


def test_check_stock_by_ndc_sums_across_locations(monkeypatch):
    from contextlib import contextmanager
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    from medstock_shared.ai.tools.pharmacy import CheckStockArgs, check_stock_by_ndc

    rows = [
        ("main-pharmacy", 30, datetime(2026, 8, 18, tzinfo=timezone.utc)),
        ("icu", 12, datetime(2026, 8, 17, tzinfo=timezone.utc)),
    ]

    @contextmanager
    def fake_scope(*args, **kwargs):
        session = MagicMock()
        session.execute.return_value.all.return_value = rows
        yield session

    monkeypatch.setattr("medstock_shared.ai.tools.pharmacy.session_scope", fake_scope)

    result = check_stock_by_ndc(CheckStockArgs(ndc="00069406101"), PHARMACIST)
    assert result == {
        "ndc": "00069406101",
        "total_quantity": 42,
        "locations": [
            {"location_id": "main-pharmacy", "quantity": 30, "updated_at": "2026-08-18T00:00:00+00:00"},
            {"location_id": "icu", "quantity": 12, "updated_at": "2026-08-17T00:00:00+00:00"},
        ],
    }


def test_sweep_shelf_certificates_separates_flagged_from_unknown(monkeypatch):
    from contextlib import contextmanager

    from medstock_shared.ai.tools.pharmacy import SweepShelfArgs, sweep_shelf_certificates

    ndcs = ["red-ndc", "green-ndc", "unknown-ndc"]

    @contextmanager
    def fake_tenant_scope(*args, **kwargs):
        session = MagicMock()
        session.scalars.return_value.all.return_value = ndcs
        session.execute.return_value.all.return_value = [("red-ndc", 10), ("green-ndc", 5)]
        yield session

    monkeypatch.setattr(
        "medstock_shared.ai.tools.pharmacy.session_scope", fake_tenant_scope
    )

    records = [
        SimpleNamespace(ndc="red-ndc", status="red"),
        SimpleNamespace(ndc="green-ndc", status="green"),
    ]
    findings = [("red-ndc", "RECALL_CLASS_I")]
    responses = iter([SimpleNamespace(scalars=lambda: records), findings])

    class _FakeReferenceSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, stmt):
            return next(responses)

    monkeypatch.setattr(
        "medstock_shared.ai.tools.pharmacy.Session", lambda engine: _FakeReferenceSession()
    )

    result = sweep_shelf_certificates(SweepShelfArgs(), PHARMACIST)
    assert result["checked"] == 3
    assert result["unknown"] == ["unknown-ndc"]
    assert [f["ndc"] for f in result["flagged"]] == ["red-ndc"]
    assert result["flagged"][0]["codes"] == ["RECALL_CLASS_I"]
    assert result["flagged"][0]["quantity"] == 10
    # green-ndc never appears anywhere -- neither flagged nor treated as unknown
    assert all(f["ndc"] != "green-ndc" for f in result["flagged"])


def test_list_storage_excursions_caps_and_passes_facility_through(monkeypatch):
    from contextlib import contextmanager

    from medstock_shared.ai.tools.pharmacy import StorageExcursionArgs, list_storage_excursions

    rows = [{"ndc": f"n{i}", "hours": 100 - i} for i in range(40)]
    captured = {}

    def fake_excursions(session, facility_id):
        captured["facility_id"] = facility_id
        return list(rows)

    monkeypatch.setattr("medstock_shared.ai.tools.pharmacy.excursions", fake_excursions)

    @contextmanager
    def fake_scope(*args, **kwargs):
        yield MagicMock()

    monkeypatch.setattr("medstock_shared.ai.tools.pharmacy.session_scope", fake_scope)

    result = list_storage_excursions(StorageExcursionArgs(facility_id=3), PHARMACIST)
    assert captured["facility_id"] == 3
    assert result["checked"] == 40
    assert len(result["excursions"]) == 30
    assert result["truncated"] is True


def test_explore_ndc_passes_the_ndc_through_to_the_promoted_explore(monkeypatch):
    from medstock_shared.ai.tools.pharmacy import ExploreNdcArgs, explore_ndc

    captured = {}

    def fake_explore(session, ndc):
        captured["session"] = session
        captured["ndc"] = ndc
        return {"ndc": ndc, "status": "green", "provenance": "on_demand", "codes": []}

    monkeypatch.setattr("medstock_shared.ai.tools.pharmacy.explore", fake_explore)
    monkeypatch.setattr("medstock_shared.ai.tools.pharmacy.Session", lambda engine: MagicMock())

    result = explore_ndc(ExploreNdcArgs(ndc="00069406101"), PHARMACIST)
    assert captured["ndc"] == "00069406101"
    assert result["status"] == "green"


def test_get_patient_regimen_never_returns_name_or_dob(monkeypatch):
    """The point of this tool: a PHI-boundary test, not a formality."""
    from contextlib import contextmanager
    from datetime import date

    from medstock_shared.ai.tools.pharmacy import PatientRegimenArgs, get_patient_regimen

    patient_id = "11111111-1111-1111-1111-111111111111"
    row = SimpleNamespace(
        hospital_id="hospital-1",
        full_name="Jane Real Patient",
        date_of_birth=date(1970, 1, 1),
        blood_group="O+",
        allergy_codes=["penicillin"],
        condition_codes=["ckd"],
        pgx_phenotypes=["CYP2C19:Poor Metabolizer"],
    )

    @contextmanager
    def fake_scope(*args, **kwargs):
        session = MagicMock()
        session.get.return_value = row
        yield session

    monkeypatch.setattr("medstock_shared.ai.tools.pharmacy.session_scope", fake_scope)

    physician = Principal("user-2", "hospital-1", "physician")
    result = get_patient_regimen(PatientRegimenArgs(patient_id=patient_id), physician)

    assert "full_name" not in result
    assert "date_of_birth" not in str(result)
    assert "Jane" not in str(result)
    assert result["allergy_codes"] == ["penicillin"]
    assert result["age_band"] in {"18-39", "40-64", "65-74", "75-89", "90+"}


def test_declarations_are_scoped_to_the_caller_role():
    """No mocking -- the real registry, populated by the real pharmacy.py."""
    from medstock_shared.ai.tools import declarations_for

    names = {d["name"] for d in declarations_for(PHARMACIST)}
    assert names == {
        "search_analogues_rxnorm",
        "verify_batch_cert",
        "check_stock_by_ndc",
        "sweep_shelf_certificates",
        "explore_ndc",
        "list_storage_excursions",
    }
    assert declarations_for(Principal("u", "h", "not-a-real-role")) == []


def test_denied_tools_for_is_the_complement_of_declarations_for():
    """Pharmacist lacks patient:read, so get_patient_regimen is the one tool
    denied to the role with the widest permission set in the system."""
    from medstock_shared.ai.tools import denied_tools_for

    assert {d["name"] for d in denied_tools_for(PHARMACIST)} == {"get_patient_regimen"}
    names = {d["name"] for d in denied_tools_for(Principal("u", "h", "not-a-real-role"))}
    assert names == {
        "search_analogues_rxnorm",
        "verify_batch_cert",
        "check_stock_by_ndc",
        "sweep_shelf_certificates",
        "explore_ndc",
        "get_patient_regimen",
        "list_storage_excursions",
    }


def test_system_prompt_names_role_gated_tools_the_model_may_not_call(monkeypatch, audit_calls):
    """A role missing one tool's permission still gets the *other* tool
    offered as callable, and the missing one named (not offered) in the
    system prompt -- so the model can tell the user "you don't have
    permission for that" instead of hallucinating or going vague."""
    import medstock_shared.auth as auth_module

    gapped_role = "physician"  # holds certificate:read today; test removes it
    monkeypatch.setitem(
        auth_module.PERMS, gapped_role, auth_module.PERMS[gapped_role] - {"certificate:read"}
    )
    principal = Principal("user-2", "hospital-1", gapped_role)

    fake = _FakeClient(turns=[[_FakeChunk(text="hi")]])
    monkeypatch.setattr("app.copilot.client", lambda: fake)

    res = _client(principal).post("/copilot/chat", json={"messages": [{"role": "user", "text": "hi"}]})
    assert res.status_code == 200

    instruction = fake.configs[0].system_instruction
    assert "verify_batch_cert" in instruction
    assert "don't have permission" in instruction
    # still-granted tools stay callable, not just named in the prompt
    declared_names = {d.name for d in fake.configs[0].tools[0].function_declarations}
    assert declared_names == {
        "search_analogues_rxnorm",
        "check_stock_by_ndc",
        "sweep_shelf_certificates",
        "get_patient_regimen",
        "list_storage_excursions",
    }
