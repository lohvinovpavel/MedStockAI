# `auth` — implementation plan

Companion to [auth-spec.md](auth-spec.md). The spec says *what* and *why*; this document says
*exactly which lines to write*. Seven PRs, in order.

**Read this section before writing any code.**

---

## Rules for the implementer

1. **Do the PRs in order.** PR 2 cannot run before PR 1 lands; PR 3's tests cannot pass before
   PR 2 lands. There is no parallelism here.
2. **Code blocks in this document are the code to write**, not sketches. Where a block says
   *"replace lines X–Y"*, replace exactly those lines and leave the rest of the file alone.
3. **Do not add files that are not listed.** No `services/auth/app/crud.py`, no
   `services/auth/app/dependencies.py`, no `models/` package. Every file this work needs is named
   below, and there are five new ones total.
4. **Do not "improve" anything you pass on the way.** If you see something in `shared/` or another
   service that looks wrong and is not in this document, leave it and write it down at the bottom
   of your PR description.
5. **After each PR, run the verification block for that PR.** If it does not pass, fix it before
   starting the next PR. Do not proceed on a red check.
6. Line length is 100 (`pyproject.toml` `[tool.ruff]`). `uv run ruff check .` must pass.

## Prerequisites — do this once, before PR 1

`uv` is the package manager for this repo. Install it, then a local Postgres.

```bash
pip install uv
```

**Postgres runs natively on this machine — no Docker.** Install PostgreSQL 16, then create the role
and database the connection string expects:

```bash
winget install --id PostgreSQL.PostgreSQL.16 --silent --accept-package-agreements --accept-source-agreements
```

```bash
psql -U postgres -c "CREATE ROLE medstock LOGIN PASSWORD 'medstock' SUPERUSER" -c "CREATE DATABASE medstock OWNER medstock"
```

`SUPERUSER` is a local-development convenience so `CREATE EXTENSION citext` cannot fail. The
deployed app role is the opposite of this — no `BYPASSRLS`, not a table owner (`docs/services.md`
§1.2). Never mirror this grant into a real environment.

`psql` lives in `C:\Program Files\PostgreSQL\16\bin`; add it to `PATH` if the command is not found.

Then, in **every** shell you use for this work:

```bash
export DATABASE_URL="postgresql+psycopg://medstock:medstock@localhost:5432/medstock"
```

Check it all works before continuing:

```bash
uv sync --all-packages
```

---

## Two defects found while planning — both are fixed inside PR 1 and PR 5

These are not new features. They are existing bugs that block this work, so they are named here
rather than discovered mid-task.

### Defect A — importing `medstock_shared` crashes without a Gemini key

`shared/medstock_shared/__init__.py` imports `.ai`, and `shared/medstock_shared/ai.py` builds the
client at module import time:

```python
_client = genai.Client(api_key=settings.gemini_api_key)   # line 30
```

With `gemini_api_key` unset this raises `ValueError: No API key was provided` (verified directly).
So **`import medstock_shared` fails today unless `GEMINI_API_KEY` is set** — which means
`services/auth/app/main.py` cannot be imported, `test_health.py` cannot run, and CI's
`uv run pytest -q` cannot pass, for all seven services. CI is `workflow_dispatch`-only, which is
why nobody has hit it yet.

Fixed in **PR 1** by making the client lazy. One function in one file; all eight images get it.

### Defect B — the Ingress does not strip `/api/<service>`

`deploy/k8s/ingress.yaml` routes `/api/auth` (`pathType: Prefix`) to the `auth` Service with **no
`rewrite-target` annotation**. nginx-ingress passes the original path upstream unchanged, so the
pod receives `/api/auth/healthz` — but `services/auth/app/main.py` declares `@app.get("/healthz")`.
Every browser call through the Ingress is a 404 today, in all seven services. `web/components/
ServiceHealth.tsx` already calls `apiFetch("auth", "/healthz")`, so the existing health widget is
broken in a deployed cluster.

Two ways to fix it: add the prefix inside each FastAPI app (seven files, six owned by other people),
or make the Ingress strip it (one file, fixes all seven). Take the second.

Fixed in **PR 5**. Until then, `docs/services.md` §3's endpoint sketch (`POST /login`, not
`POST /api/auth/login`) is what the code should implement — PR 5 is what makes that true.

---

# PR 1 — `shared/` (models, config, and the two shared fixes)

**Files touched:** `shared/medstock_shared/models.py`, `shared/medstock_shared/config.py`,
`shared/medstock_shared/auth.py`, `shared/medstock_shared/ai.py`,
`shared/medstock_shared/__init__.py`.

All four `shared/` edits are in this one PR on purpose. A change to `shared/` redeploys all seven
services (`docs/services.md` §6); doing it once costs one redeploy instead of three.

### 1.1 — `shared/medstock_shared/models.py`

**Change the import block at the top of the file** (currently lines 6–9) to:

```python
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
```

**Append these three classes to the end of the file.** Do not modify `AICache` or any of the four
reference tables.

```python
# --- Identity tables (docs/auth-spec.md §1): owned by `auth`, and the one
# documented exception to the "always go through session_scope" rule. Login
# runs *before* there is a hospital_id to set, so these three carry no RLS
# policies and are queried through SessionLocal directly.


class Hospital(Base):
    __tablename__ = "hospital"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AppUser(Base):
    """Not `user` — reserved word in Postgres."""

    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # CITEXT so Ann@x.org and ann@x.org cannot become two accounts. The
    # migration creates the extension before this table.
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Membership(Base):
    """Role belongs to the membership, not the user — "director at A,
    pharmacist at B" is the case that would otherwise force an auth rewrite.

    `uq_membership_one_hospital_per_user` is the "one hospital per user"
    decision (docs/services.md §8 #4). Dropping that one constraint plus
    adding a hospital picker at login is the whole multi-hospital change.
    """

    __tablename__ = "membership"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True
    )
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospital.id"), primary_key=True
    )
    # Must stay in sync with the keys of PERMS in auth.py.
    role: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "role IN ('pharmacist','physician','director','admin')", name="ck_membership_role"
        ),
        UniqueConstraint("user_id", name="uq_membership_one_hospital_per_user"),
    )
```

> **Note for the implementer:** the spec said `gen_random_uuid()` as a server default. Use
> `default=uuid.uuid4` (Python-side) as written above instead — it means the tables can be created
> with `create_all()` in tests without a Postgres function dependency, and it is one less thing to
> get wrong. Everything else about the spec's §1 table definitions is unchanged.

### 1.2 — `shared/medstock_shared/config.py`

Add two fields to `Settings`, directly below `jwt_algorithm`:

```python
    # auth only — the private key never leaves that service. The other six
    # hold jwt_public_key and nothing else.
    jwt_private_key: str = ""
    jwt_ttl_hours: int = 8
```

### 1.3 — `shared/medstock_shared/auth.py` (Defect B's sibling: the cookie fallback)

`current_principal` currently reads the `Authorization` header only, but `docs/services.md` §2
specifies an httpOnly session cookie. **Replace the first four lines of the `current_principal`
body** — that is:

```python
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        claims = jwt.decode(
            header[7:],
```

with:

```python
    header = request.headers.get("authorization", "")
    # Cookie is how the browser authenticates (httpOnly, docs/services.md §2).
    # The Bearer header stays for curl and for local dev token minting — one
    # `or`, and both paths land on the same verification below.
    token = header[7:] if header.startswith("Bearer ") else request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="missing credentials")
    try:
        claims = jwt.decode(
            token,
```

The rest of the `jwt.decode(...)` call and everything after it is unchanged.

Then add this constant immediately above the `Principal` dataclass:

```python
# The one place the cookie name is written. auth sets it; all seven read it.
COOKIE_NAME = "medstock_token"
```

### 1.4 — `shared/medstock_shared/ai.py` (Defect A)

**Delete line 30:**

```python
_client = genai.Client(api_key=settings.gemini_api_key)
```

**Add in its place:**

```python
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Built on first use, not at import. Five of the seven services have no
    Gemini key and must still be able to `import medstock_shared`."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client
```

Then in `_generate_json`, change:

```python
        response = _client.models.generate_content(
```

to:

```python
        response = _get_client().models.generate_content(
```

> `_get_client()` is called inside the `try` in `_generate_json`, so a missing key now surfaces as
> an `AIError` from `ask_ai()` — which callers already handle — instead of an import crash. That is
> the correct behaviour, not a side effect to work around.

### 1.5 — `shared/medstock_shared/__init__.py`

Update the imports and `__all__` to export the new names. The whole file becomes:

```python
from .ai import AIError, ask_ai, dedupe_key
from .ai_tasks import TASKS, AITask
from .auth import COOKIE_NAME, Principal, current_principal, require
from .config import Settings, settings
from .db import engine, session_scope
from .models import AICache, AppUser, Base, Hospital, Membership

__all__ = [
    "AICache", "AIError", "AITask", "AppUser", "Base", "COOKIE_NAME", "Hospital",
    "Membership", "Principal", "Settings", "TASKS", "ask_ai", "current_principal",
    "dedupe_key", "engine", "require", "session_scope", "settings",
]
```

### Verify PR 1

```bash
uv run ruff check . && uv run python -c "import medstock_shared; print(medstock_shared.COOKIE_NAME)"
```

Must print `medstock_token` with **no** `GEMINI_API_KEY` set. If it raises `ValueError: No API key
was provided`, step 1.4 is wrong.

```bash
uv run pytest -q
```

The seven existing `test_health.py` files must pass. Before PR 1 they could not even import.

---

# PR 2 — the initial migration

**Files touched:** one new file under `migrations/versions/`.

This is the repo's **first** Alembic revision. `migrations/versions/` contains only `.gitkeep`, so
this revision creates *every* table in `Base.metadata` — `ai_cache`, the four reference tables, and
the three identity tables — not just auth's three.

> **Spec correction:** `auth-spec.md` §7 suggested landing the RLS policies (open item #2) in this
> same revision. Do **not**. There are no tenant tables in `shared/medstock_shared/models.py` yet
> (`formulary_item`, `stock_snapshot`, `recommendation`, `review_decision`, `audit_log_entry`,
> `mapping_spec` do not exist), so there is nothing for a policy to attach to. RLS lands with the
> revision that creates those tables. Auth's three identity tables are RLS-exempt by design (§1).

### 2.1 — generate the skeleton

Make sure the database is **empty** first, or autogenerate will produce a diff instead of a full
schema:

```bash
psql -U medstock -d medstock -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
```

```bash
uv run alembic revision --autogenerate -m "initial schema"
```

This writes `migrations/versions/<hash>_initial_schema.py`. **Do not invent the revision hash
yourself** — let Alembic generate it. `down_revision` must be `None`.

### 2.2 — edit the generated file

Open the generated file and make exactly two changes.

**First**, as the very first statement inside `upgrade()`, before any `op.create_table`:

```python
    # AppUser.email is CITEXT. The extension has to exist before the table
    # that uses it. Available in the postgres:16 image and on Cloud SQL.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
```

**Second**, as the very last statement inside `downgrade()`, after all `op.drop_table` calls:

```python
    op.execute("DROP EXTENSION IF EXISTS citext")
```

Then read the rest of the generated file and confirm it contains:

- `op.create_table('hospital', ...)`, `op.create_table('app_user', ...)`,
  `op.create_table('membership', ...)` — with `hospital` and `app_user` **before** `membership`
  (Alembic sorts by foreign-key dependency; if it did not, reorder them by hand),
- `op.create_table('ai_cache', ...)` plus `drug`, `shortage_event`, `drug_price`, `rxnorm_edge`,
- on `membership`: `sa.UniqueConstraint('user_id', name='uq_membership_one_hospital_per_user')`
  and `sa.CheckConstraint(...)` named `ck_membership_role`,
- on `app_user`: `postgresql.CITEXT()` for `email`.

If any of those is missing, PR 1 was not saved correctly — fix PR 1 rather than hand-writing the
migration.

### Verify PR 2

```bash
uv run alembic upgrade head && uv run alembic downgrade base && uv run alembic upgrade head
```

All three must succeed. The down-then-up proves `downgrade()` is real, which matters because this
is the revision everyone else's first migration will build on.

```bash
psql -U medstock -d medstock -c "\d membership"
```

Confirm the unique constraint on `user_id` and the check constraint on `role` are both listed.

---

# PR 3 — `/login`, `/logout`, `/me`

**Files touched:** three new files in `services/auth/app/`, one rewrite of `main.py`, two new test
files. `services/auth/pyproject.toml` already has `argon2-cffi` — **add no dependencies.**

### 3.1 — new file `services/auth/app/security.py`

```python
"""Password hashing and token minting. The only module in the system that
touches the private key."""

from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher

from medstock_shared import settings

# Library defaults, not hand-tuned parameters: they track the RFC 9106
# recommendation and are maintained by people who follow it. A time_cost
# picked by us is a number nobody would ever revisit.
_ph = PasswordHasher()

# Verified against when the email is unknown, so an unknown email and a wrong
# password take the same time and /login is not an account-existence oracle.
DUMMY_HASH = _ph.hash("not-a-real-password")

MAX_FAILED_ATTEMPTS = 10
LOCKOUT = timedelta(minutes=15)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        _ph.verify(stored_hash, password)
        return True
    except Exception:
        # Wrong password, or a stored hash we cannot parse. Both are a failed
        # login, never a 500 — a corrupt hash must not become an outage.
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when the library's defaults have moved since this hash was made.
    The only migration path a password store gets."""
    return _ph.check_needs_rehash(stored_hash)


def mint_token(user_id: str, hospital_id: str, role: str) -> tuple[str, datetime]:
    """The claim set is exactly what current_principal reads, plus iat/exp.
    Nothing else — an email in a token is a claim that goes stale."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=settings.jwt_ttl_hours)
    token = jwt.encode(
        {
            "sub": user_id,
            "hospital_id": hospital_id,
            "role": role,
            "aud": "medstock",
            "iat": now,
            "exp": expires_at,
        },
        settings.jwt_private_key,
        algorithm=settings.jwt_algorithm,
    )
    return token, expires_at
```

### 3.2 — new file `services/auth/app/schemas.py`

```python
"""Response models are the leak guard: a column added to app_user later
cannot reach the browser unless it is named here."""

from datetime import datetime

from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    """No token field. The token goes in the httpOnly cookie only."""

    user_id: str
    hospital_id: str
    role: str
    expires_at: datetime


class MeResponse(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    role: str
    hospital_id: str
    hospital_name: str
```

> `email` is a plain `str`, not `EmailStr`. `EmailStr` needs the `email-validator` package, and a
> malformed email simply fails to match a row and returns the same 401 as any other bad login. Do
> not add the dependency.

### 3.3 — rewrite `services/auth/app/main.py`

Replace the whole file with:

```python
import uuid
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException, Response
from sqlalchemy import select, text

from medstock_shared import COOKIE_NAME, Principal, current_principal, engine, settings
from medstock_shared.db import SessionLocal
from medstock_shared.models import AppUser, Hospital, Membership

from .schemas import LoginRequest, LoginResponse, MeResponse
from .security import (
    DUMMY_HASH,
    LOCKOUT,
    MAX_FAILED_ATTEMPTS,
    hash_password,
    mint_token,
    needs_rehash,
    verify_password,
)

app = FastAPI(title="auth")

INVALID = HTTPException(status_code=401, detail="invalid credentials")
"""One error for unknown email, wrong password, inactive, and locked. Telling
them apart turns /login into an account-existence oracle."""


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. No dependencies checked on purpose —
    a database blip must not get every pod restarted."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, response: Response) -> LoginResponse:
    """Uses SessionLocal directly, not session_scope: this runs before there
    is a hospital_id to set, and the identity tables carry no RLS policies.
    docs/auth-spec.md §1 is the only place that exception is granted.
    """
    now = datetime.now(UTC)

    with SessionLocal() as s:
        row = s.execute(
            select(AppUser, Membership)
            .join(Membership, Membership.user_id == AppUser.id)
            .where(AppUser.email == body.email.strip())
        ).one_or_none()

        if row is None:
            verify_password(DUMMY_HASH, body.password)
            raise INVALID

        user, membership = row

        if not user.is_active or (user.locked_until is not None and user.locked_until > now):
            verify_password(DUMMY_HASH, body.password)
            raise INVALID

        if not verify_password(user.password_hash, body.password):
            user.failed_attempts += 1
            if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
                user.locked_until = now + LOCKOUT
                user.failed_attempts = 0
            s.commit()
            raise INVALID

        user.failed_attempts = 0
        user.locked_until = None
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(body.password)
        s.commit()

        # Read out inside the session; the response is built after it closes.
        user_id, hospital_id, role = str(user.id), str(membership.hospital_id), membership.role

    token, expires_at = mint_token(user_id, hospital_id, role)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.jwt_ttl_hours * 3600,
        httponly=True,   # not readable by any script on the page
        secure=True,     # Chrome exempts http://localhost, so dev still works
        samesite="lax",
        path="/",
    )
    return LoginResponse(
        user_id=user_id, hospital_id=hospital_id, role=role, expires_at=expires_at
    )


@app.post("/logout", status_code=204)
def logout(response: Response) -> None:
    """Takes no token on purpose — logging out with an expired one must still
    clear the cookie. With no revocation list this clears the browser's copy
    and nothing more; docs/auth-spec.md §2 says so out loud."""
    response.delete_cookie(COOKIE_NAME, path="/")


@app.get("/me", response_model=MeResponse)
def me(principal: Principal = Depends(current_principal)) -> MeResponse:
    """Reads live rather than echoing the claims, so a deactivated user stops
    working on the next request instead of at token expiry."""
    try:
        user_id = uuid.UUID(principal.user_id)
    except ValueError as exc:
        raise INVALID from exc

    with SessionLocal() as s:
        row = s.execute(
            select(AppUser, Membership, Hospital)
            .join(Membership, Membership.user_id == AppUser.id)
            .join(Hospital, Hospital.id == Membership.hospital_id)
            .where(AppUser.id == user_id, AppUser.is_active.is_(True))
        ).one_or_none()

        if row is None:
            raise INVALID

        user, membership, hospital = row
        return MeResponse(
            user_id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            role=membership.role,
            hospital_id=str(hospital.id),
            hospital_name=hospital.name,
        )
```

> Two things not to "clean up": `INVALID` is a module-level exception instance reused by every
> failure path — that is what keeps the four failure cases indistinguishable. And `/logout`
> returns `None`, not a `Response`; returning a `Response` object would discard the `delete_cookie`
> header that FastAPI injected into `response`.

### 3.4 — new file `services/auth/tests/conftest.py`

```python
"""A throwaway RSA keypair for the whole test session. The token contract
between auth and the other six is only meaningful if a real signature is
verified with a real public key, so these tests mint and verify for real."""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from medstock_shared import settings


@pytest.fixture(scope="session", autouse=True)
def jwt_keys() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings.jwt_private_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    settings.jwt_public_key = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
```

`cryptography` is already installed — it comes with `pyjwt[crypto]` in `shared/pyproject.toml`. Do
not add it as a dependency.

### 3.5 — new file `services/auth/tests/test_login.py`

```python
"""Runs against the CI Postgres (DATABASE_URL), not SQLite: `email` is CITEXT.

Only auth's three tables are created here. The reference tables use JSONB and
belong to migrations, not to this test's setup.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from medstock_shared import Base, current_principal
from medstock_shared.db import SessionLocal, engine
from medstock_shared.models import AppUser, Hospital, Membership

from app.main import app
from app.security import hash_password

PASSWORD = "correct-horse-battery-staple"


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
    client = TestClient(app)
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
```

```python
# ponytail: PasswordHasher() at library defaults costs ~50ms per verify, and
# the lockout test does eleven. If the suite gets slow, drop time_cost in
# conftest — do not weaken the production hasher.
```

### Verify PR 3

```bash
uv run alembic upgrade head && uv run pytest services/auth -q && uv run ruff check .
```

All tests must pass. `test_token_verifies_through_shared_current_principal` and
`test_cookie_alone_authenticates_another_service` are the two that must not be skipped or weakened
— they are the contract with the other six services.

---

# PR 4 — the seed script

**Files touched:** one new file, `services/auth/app/seed.py`.

```python
"""python -m app.seed — one hospital, four users, one per role.

Idempotent: running it twice against a live database is safe and changes
nothing. Not an Alembic data migration (a migration cannot print a generated
password and cannot be re-run selectively) and not a test fixture (the demo
needs these rows in the real database).

Run it by hand. Wiring it into migrate-job would put demo accounts into every
environment that ever runs a migration.
"""

import os
import secrets

from sqlalchemy import select

from medstock_shared.db import SessionLocal
from medstock_shared.models import AppUser, Hospital, Membership

from .security import hash_password

HOSPITAL_NAME = "St Mary's General"
USERS = [
    ("ann@stmarys.org", "Ann Reyes", "pharmacist"),
    ("ben@stmarys.org", "Ben Okafor", "physician"),
    ("cara@stmarys.org", "Cara Lindqvist", "director"),
    ("dan@stmarys.org", "Dan Whitfield", "admin"),
]


def main() -> None:
    password = os.environ.get("SEED_PASSWORD") or secrets.token_urlsafe(12)

    with SessionLocal() as s:
        hospital = s.execute(
            select(Hospital).where(Hospital.name == HOSPITAL_NAME)
        ).scalar_one_or_none()
        if hospital is None:
            hospital = Hospital(name=HOSPITAL_NAME)
            s.add(hospital)
            s.flush()

        created = []
        for email, full_name, role in USERS:
            if s.execute(select(AppUser).where(AppUser.email == email)).scalar_one_or_none():
                continue
            user = AppUser(email=email, password_hash=hash_password(password), full_name=full_name)
            s.add(user)
            s.flush()
            s.add(Membership(user_id=user.id, hospital_id=hospital.id, role=role))
            created.append(email)

        s.commit()

    print(f"hospital: {HOSPITAL_NAME} ({hospital.id})")
    if created:
        # Printed once and never stored. Re-running does not reprint it,
        # because it does not re-create the users.
        print(f"created: {', '.join(created)}")
        print(f"password (all four): {password}")
    else:
        print("nothing to do — all four users already exist")


if __name__ == "__main__":
    main()
```

### Verify PR 4

```bash
uv run --package medstock-auth python -m app.seed
```

Run it **twice**. The first run prints four emails and a password; the second prints
`nothing to do`. Then log in with the printed password:

```bash
uv run --package medstock-auth uvicorn app.main:app --port 8000
```

```bash
curl -i -X POST localhost:8000/login -H 'content-type: application/json' -d '{"email":"ann@stmarys.org","password":"<printed>"}'
```

The response must be `200` with a `Set-Cookie: medstock_token=...; HttpOnly; Secure; SameSite=lax`
header and **no token in the body**.

---

# PR 5 — Ingress path stripping (Defect B)

**Files touched:** `deploy/k8s/ingress.yaml` only.

The `rewrite-target` annotation applies to a whole Ingress object, and the `/` catch-all for `web`
must **not** be rewritten. So the file becomes two Ingress objects: one for the API with stripping,
one for the web app without.

Replace the entire contents of `deploy/k8s/ingress.yaml` with:

```yaml
# Same origin for browser and API: the session cookie stays httpOnly/SameSite
# and no CORS configuration exists anywhere in the system.
#
# Two objects, not one: rewrite-target is per-Ingress, and the /api/* paths
# need the prefix stripped while web's / catch-all must not be touched.
# Without the strip, a pod receives /api/auth/login while its route is
# declared as /login — every browser call is a 404 (docs/auth-spec.md,
# Defect B).
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: medstock-api
  annotations:
    nginx.ingress.kubernetes.io/use-regex: "true"
    nginx.ingress.kubernetes.io/rewrite-target: /$2
spec:
  rules:
  - http:
      paths:
      - { path: "/api/auth(/|$)(.*)",       pathType: ImplementationSpecific, backend: { service: { name: auth,              port: { number: 8000 } } } }
      - { path: "/api/inventory(/|$)(.*)",  pathType: ImplementationSpecific, backend: { service: { name: inventory,         port: { number: 8000 } } } }
      - { path: "/api/analogue(/|$)(.*)",   pathType: ImplementationSpecific, backend: { service: { name: analogue,          port: { number: 8000 } } } }
      - { path: "/api/compliance(/|$)(.*)", pathType: ImplementationSpecific, backend: { service: { name: compliance,        port: { number: 8000 } } } }
      - { path: "/api/patients(/|$)(.*)",   pathType: ImplementationSpecific, backend: { service: { name: patient-profiling, port: { number: 8000 } } } }
      - { path: "/api/prediction(/|$)(.*)", pathType: ImplementationSpecific, backend: { service: { name: prediction,        port: { number: 8000 } } } }
      - { path: "/api/warehouse(/|$)(.*)",  pathType: ImplementationSpecific, backend: { service: { name: warehouse,         port: { number: 8000 } } } }
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: medstock-web
spec:
  rules:
  - http:
      paths:
      - { path: /, pathType: Prefix, backend: { service: { name: web, port: { number: 3000 } } } }
```

Every capture group is `$2` because every API path uses the identical `(/|$)(.*)` shape — that
consistency is what makes one annotation work for all seven.

### Verify PR 5

```bash
kubectl apply --dry-run=client -f deploy/k8s/ingress.yaml
```

Also check `deploy/k8s/kustomization.yaml` still lists `ingress.yaml` (it does — the filename did
not change) and that nothing references the old Ingress name `medstock`:

```bash
grep -rn "name: medstock$" deploy/k8s/
```

This PR fixes the browser path for all seven services, not just auth. Say so in the PR description
so the other three owners know their `ServiceHealth` widget starts working.

---

# PR 6 — the web login page

**Files touched:** `web/lib/api.ts`, `web/next.config.mjs`, `web/app/auth/page.tsx`.

### 6.1 — `web/lib/api.ts`

Replace the whole file:

```typescript
import { SERVICES, ServiceName } from "./services";

/**
 * Every backend call goes through this. Same-origin by design — no base
 * URL, Ingress routes /api/<service> to the right pod (docs/services.md §2).
 * Use it from every service page below so there is exactly one place that
 * knows how a request is authenticated.
 *
 * Authentication is the medstock_token cookie: httpOnly, so no code here
 * can read it and none needs to — the browser attaches it. That is the
 * point (docs/auth-spec.md §4). Nothing in web/ ever holds a token.
 */
export async function apiFetch(service: ServiceName, path: string, init?: RequestInit) {
  const res = await fetch(`${SERVICES[service]}${path}`, {
    ...init,
    credentials: "include",
    headers: { "content-type": "application/json", ...init?.headers },
  });

  if (!res.ok) {
    throw new Error(`${service}${path} -> ${res.status} ${res.statusText}`);
  }
  return res.status === 204 ? null : res.json();
}
```

The `localStorage` read is gone, along with the `ponytail:` comment that asked for exactly this
change once `/login` existed.

### 6.2 — `web/next.config.mjs`

```javascript
/** @type {import('next').NextConfig} */
export default {
  // Required by the runtime stage of web/Dockerfile.
  output: "standalone",

  // Dev only. In a cluster the Ingress owns /api/* and never reaches Next
  // (deploy/k8s/ingress.yaml), so this list is empty in production.
  // ponytail: every service maps to one local port, so only one backend can
  // run at a time locally. Split it per service when two people need two
  // backends up at once.
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [{ source: "/api/:service/:path*", destination: "http://localhost:8000/:path*" }];
  },
};
```

The rewrite drops the `:service` segment, matching what the Ingress now does in PR 5. Same shape in
both environments.

### 6.3 — `web/app/auth/page.tsx`

```tsx
"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api";

// Owner: Tymur. Backend: services/auth (Ingress path /api/auth).
// The token is never touched here — /login sets an httpOnly cookie the
// browser attaches to every later apiFetch (docs/auth-spec.md §4).
export default function AuthPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [me, setMe] = useState<Record<string, string> | null>(null);
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await apiFetch("auth", "/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setMe(await apiFetch("auth", "/me"));
    } catch {
      // The backend returns one message for every failure on purpose; do not
      // try to say more here than it does.
      setError("invalid credentials");
    }
  }

  async function logout() {
    await apiFetch("auth", "/logout", { method: "POST" });
    setMe(null);
  }

  if (me) {
    return (
      <main>
        <h1>auth</h1>
        <p>
          {me.full_name} — {me.role} at {me.hospital_name}
        </p>
        <button onClick={logout}>log out</button>
      </main>
    );
  }

  return (
    <main>
      <h1>auth</h1>
      <form onSubmit={submit}>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="email"
          autoComplete="username"
          required
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="password"
          autoComplete="current-password"
          required
        />
        <button type="submit">log in</button>
      </form>
      {error && <p role="alert">{error}</p>}
    </main>
  );
}
```

The `ServiceHealth` widget comes off this page — it was a placeholder proving the wiring, and a real
login form proves considerably more.

### Verify PR 6

```bash
cd web && npm install && npm run build
```

Then, with the backend running (`uv run --package medstock-auth uvicorn app.main:app --port 8000`)
and `npm run dev` in `web/`, open `http://localhost:3000/auth` and log in with a seeded account.
You must see the name, role, and hospital, and `log out` must return you to the form.

In DevTools → Application → Cookies, `medstock_token` must show **HttpOnly ✓**. If it is missing
entirely, your browser is rejecting `Secure` on `http://localhost` — use Chrome, which exempts
localhost.

---

# PR 7 — the keypair runbook

**Files touched:** `services/auth/README.md` only.

The README currently tells developers to `cat path/to/dev-private-key.pem`, and no such file exists
or can be generated from the instructions. Add this section immediately above `## Local development`:

````markdown
## Generating the signing keypair

`auth` holds the private key; all eight deployments hold the public one. RS256,
because the alternative (HS256) means shipping a signing secret to seven
services that only need to verify.

```bash
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out jwt-private-key.pem
openssl rsa -pubout -in jwt-private-key.pem -out jwt-public-key.pem
```

Into the cluster — the two k8s objects `deploy/k8s/auth.yaml` already
references by name:

```bash
kubectl create secret generic medstock-auth --from-file=jwt-private-key=jwt-private-key.pem
kubectl create configmap medstock-jwt --from-file=public-key=jwt-public-key.pem
```

Delete `jwt-private-key.pem` from your machine afterwards. It is not in
`.gitignore` by name — do not rely on that.

**Rotation is a manual outage window.** Generate a new pair, replace both
objects, roll all eight deployments, and everyone logs in again. There is no
`kid`-based dual-key verification; build it when a second key must exist at
the same time as the first.

### For local development

Generate a **separate** pair with the same two commands and point the two env
vars at it. Never use the cluster's private key locally:

```bash
export JWT_PRIVATE_KEY="$(cat jwt-private-key.pem)"
export JWT_PUBLIC_KEY="$(cat jwt-public-key.pem)"
```
````

Then, in the existing `## Local development` section, replace
`path/to/dev-private-key.pem` with `jwt-private-key.pem` in both places, and in
`## Known gaps`, delete the bullet that begins **"`auth` itself is unbuilt"** — after PR 6 it is
false. Leave the other two gap bullets (RLS, revocation); both are still true.

### Verify PR 7

Hand the README to someone who has not read this document. They should be able to produce both files
and both k8s objects without asking a question.

---

## Deviations from `auth-spec.md` §7, and why

The spec listed seven steps; this is seven PRs, but not the same seven.

| Spec step | Here | Why |
|---|---|---|
| 1 (models) + 4 (cookie fallback) | Merged into **PR 1** | Both are `shared/` edits, and each `shared/` change redeploys all seven services. One PR, one redeploy, instead of two |
| — | **PR 1** also fixes Defect A | `import medstock_shared` crashes without a Gemini key, so nothing in PR 3 could be tested until it is fixed |
| 2 (migration) | **PR 2**, minus RLS | §7 recommended folding open item #2 in. There are no tenant tables in the schema yet, so there is nothing for a policy to attach to |
| 3 (endpoints) | **PR 3**, tests included | The spec put tests in §8; they belong in the PR whose behaviour they check |
| — | **PR 5** is new | Defect B: the Ingress never strips `/api/<service>`, so the browser cannot reach any endpoint in any service |
| 5, 6, 7 | **PR 4, 6, 7** | Renumbered only |

If you disagree with any of these, change `auth-spec.md` §7 to match before writing code — the two
documents must not drift.
