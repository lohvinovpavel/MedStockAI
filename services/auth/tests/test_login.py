"""Runs against the CI Postgres (DATABASE_URL), not SQLite: `email` is CITEXT.

Only auth's three tables are created here. The reference tables use JSONB and
belong to migrations, not to this test's setup.
"""

import uuid

import pytest
from app.main import app
from app.security import hash_password
from fastapi.testclient import TestClient
from medstock_shared import Base, current_principal
from medstock_shared.db import SessionLocal, engine
from medstock_shared.models import AppUser, Hospital, Membership
from sqlalchemy import text

PASSWORD = "correct-horse-battery-staple"


def test_conftest_resolved_the_right_app() -> None:
    """Guard for the sys.path line in conftest.py. Seven services install a
    top-level package named `app`; without that line `from app.main import app`
    above imports analogue's application and every test below is meaningless.

    It lives here rather than in conftest.py because pytest imports conftest as
    a plugin and never collects test functions from it. Needs no database — if
    it fails, ignore every other result in the run until it is green."""
    assert app.title == "auth"


@pytest.fixture
def account() -> str:
    """A fresh user with a unique email, so tests need no teardown and cannot
    interfere with each other."""
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
    Base.metadata.create_all(
        engine, tables=[Hospital.__table__, AppUser.__table__, Membership.__table__]
    )

    email = f"{uuid.uuid4()}@test.org"
    with SessionLocal() as s:
        hospital = Hospital(name="Test Hospital")
        user = AppUser(email=email, password_hash=hash_password(PASSWORD), full_name="Ann Reyes")
        s.add_all([hospital, user])
        s.flush()
        s.add(Membership(user_id=user.id, hospital_id=hospital.id, role="pharmacist"))
        s.commit()
    return email


def test_login_round_trip(account: str) -> None:
    # https base_url, not the http default: the login cookie is Secure
    # (main.py — intentional, see infra/README.md §13), and httpx correctly
    # won't replay a Secure cookie of its own accord over plain http on the
    # /me call below. Every other test here reads the cookie out manually
    # instead of relying on automatic replay, so this is the only one that
    # needs it.
    client = TestClient(app, base_url="https://testserver")
    login = client.post("/login", json={"email": account, "password": PASSWORD})
    assert login.status_code == 200
    assert login.json()["role"] == "pharmacist"
    assert "token" not in login.text  # the token goes in the cookie, never the body

    cookie = client.cookies.get("medstock_token")
    assert cookie is not None

    me = client.get("/me")  # TestClient replays the cookie
    assert me.status_code == 200
    assert me.json()["email"] == account
    assert me.json()["hospital_name"] == "Test Hospital"
    set_cookie = me.headers.get("set-cookie") or ""
    assert "medstock_token=" in set_cookie
    assert "Path=/" in set_cookie


def test_email_is_case_insensitive(account: str) -> None:
    resp = TestClient(app).post("/login", json={"email": account.upper(), "password": PASSWORD})
    assert resp.status_code == 200


def test_wrong_password_and_unknown_email_are_indistinguishable(account: str) -> None:
    client = TestClient(app)
    wrong = client.post("/login", json={"email": account, "password": "nope"})
    unknown = client.post("/login", json={"email": "nobody@test.org", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_lockout_after_ten_failures(account: str) -> None:
    client = TestClient(app)
    for _ in range(10):
        assert client.post("/login", json={"email": account, "password": "nope"}).status_code == 401
    # The correct password now fails too — that is the whole point.
    assert client.post("/login", json={"email": account, "password": PASSWORD}).status_code == 401


def test_me_requires_a_token() -> None:
    assert TestClient(app).get("/me").status_code == 401


def test_token_verifies_through_shared_current_principal(account: str) -> None:
    """The contract between auth and the other six services. If a claim name
    ever drifts, this is the test that fails."""
    from fastapi import Depends, FastAPI

    client = TestClient(app)
    client.post("/login", json={"email": account, "password": PASSWORD})
    token = client.cookies.get("medstock_token")

    other_service = FastAPI()

    @other_service.get("/whoami")
    def whoami(p=Depends(current_principal)) -> dict[str, str]:
        return {"hospital_id": p.hospital_id, "role": p.role}

    resp = TestClient(other_service).get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "pharmacist"


def test_cookie_alone_authenticates_another_service(account: str) -> None:
    """PR 1's cookie fallback, verified from the consuming side."""
    from fastapi import Depends, FastAPI

    client = TestClient(app)
    client.post("/login", json={"email": account, "password": PASSWORD})
    token = client.cookies.get("medstock_token")

    other_service = FastAPI()

    @other_service.get("/whoami")
    def whoami(p=Depends(current_principal)) -> dict[str, str]:
        return {"role": p.role}

    other = TestClient(other_service)
    other.cookies.set("medstock_token", token)
    assert other.get("/whoami").status_code == 200   # no Authorization header at all


# ponytail: PasswordHasher() at library defaults costs ~50ms per verify, and
# the lockout test does eleven. If the suite gets slow, drop time_cost in
# conftest — do not weaken the production hasher.
