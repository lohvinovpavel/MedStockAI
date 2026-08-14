from app.main import app
from fastapi.testclient import TestClient
from medstock_shared.auth import Principal, current_principal

PHARMACIST = Principal("user-1", "hospital-1", "pharmacist")


def _client() -> TestClient:
    app.dependency_overrides[current_principal] = lambda: PHARMACIST
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_stock_by_rxcui_empty_when_no_snapshots(monkeypatch):
    monkeypatch.setattr(
        "app.main.ndcs_for_rxcui",
        lambda rxcui: ["00113041178", "00904201559"],
    )
    monkeypatch.setattr(
        "app.main.session_scope",
        _failing_scope,
    )
    body = _client().get("/stock", params={"rxcui": "246461"}).json()
    assert body["rxcui"] == "246461"
    assert body["ndc_count"] == 2
    assert body["items"] == []


def test_stock_mounted_under_ingress_prefix(monkeypatch):
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: [])
    res = _client().get("/api/inventory/stock", params={"rxcui": "246461"})
    assert res.status_code == 200
    assert res.json()["items"] == []


def test_stock_joins_ndcs_to_snapshots(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "app.main.ndcs_for_rxcui",
        lambda rxcui: ["00113041178", "00904201559"],
    )

    row = SimpleNamespace(ndc="00113041178", quantity=42, location_id="main-pharmacy")

    @contextmanager
    def fake_scope(*args, **kwargs):
        session = MagicMock()
        session.scalars.return_value.all.return_value = [row]
        yield session

    monkeypatch.setattr("app.main.session_scope", fake_scope)
    body = _client().get("/stock", params={"rxcui": "246461"}).json()
    assert body["items"] == [
        {"ndc": "00113041178", "quantity": 42, "location_id": "main-pharmacy"}
    ]


def test_stock_requires_auth():
    app.dependency_overrides.clear()
    assert TestClient(app).get("/stock", params={"rxcui": "1"}).status_code == 401


def _failing_scope(*args, **kwargs):
    from contextlib import contextmanager

    from sqlalchemy.exc import SQLAlchemyError

    @contextmanager
    def _inner():
        raise SQLAlchemyError("no stock table in this test")
        yield  # pragma: no cover

    return _inner()
