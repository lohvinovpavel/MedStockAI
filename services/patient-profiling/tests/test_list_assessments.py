"""GET /assessments -- the index that makes /explain/{request_id} reachable
without already knowing an id. Regression coverage: this route had zero
tests, which is how a bad refactor once left `AssessmentLog` unimported here
and only ruff (not the test suite) caught it.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.main import app
from fastapi.testclient import TestClient
from medstock_shared.auth import Principal, current_principal

PHARMACIST = Principal("pharm-1", "hospital-1", "pharmacist")


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def as_role(principal: Principal) -> TestClient:
    app.dependency_overrides[current_principal] = lambda: principal
    return TestClient(app)


def _row(request_id: str, verdict: str) -> SimpleNamespace:
    return SimpleNamespace(
        request_id=request_id,
        actor_id="dr.casey.park",
        created_at=datetime(2026, 8, 18, tzinfo=UTC),
        ruleset_version="2026.08.1",
        result={"assessments": [{"rxcui": "861007", "verdict": verdict}]},
    )


def test_list_assessments_summarises_the_worst_verdict_per_row(monkeypatch):
    rows = [_row("req-1", "blocked"), _row("req-2", "green")]

    @contextmanager
    def fake_scope(_hospital, _actor):
        yield SimpleNamespace(scalars=lambda *_a, **_k: SimpleNamespace(all=lambda: rows))

    monkeypatch.setattr("app.main.session_scope", fake_scope)

    body = as_role(PHARMACIST).get("/assessments").json()
    assert [item["request_id"] for item in body["items"]] == ["req-1", "req-2"]
    assert body["items"][0]["verdict"] == "blocked"
    assert body["items"][1]["verdict"] == "green"
    assert body["items"][0]["drugs"] == ["861007"]
