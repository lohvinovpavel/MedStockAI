from app.main import app
from fastapi.testclient import TestClient


def test_healthz_needs_no_database():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}
