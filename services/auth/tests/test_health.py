from fastapi.testclient import TestClient

from app.main import app


def test_healthz_needs_no_database():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}
