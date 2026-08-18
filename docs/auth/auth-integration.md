# Integrating `auth` into your service

For the owners of `inventory`, `analogue`, `compliance`, `patient-profiling`, `prediction`, and
`warehouse`. This is the **development procedure**: how to protect a route, how to get a token, and
how to test locally.

The *contract* — claims, `Principal`, the permission map — is in
[`services/auth/README.md`](../services/auth/README.md) and is not repeated here. Read that first if
you have not.

Every command and code block below was executed against this repo. Expected output is shown where
it is worth checking.

---

## TL;DR

1. You never call `auth` over the network. You verify the token locally with a public key.
2. **You do not need to run `auth` at all** to develop your service — mint your own token (§3).
3. Protecting a route is one line: `Depends(require("your:permission"))`.

---

## 1. One-time setup

You need a keypair. `auth` signs with the private key; your service verifies with the public one.
For solo local work, generate your own pair — it does not have to match anyone else's, as long as
the same pair is used to mint and to verify.

```bash
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out jwt-private-key.pem
```

```bash
openssl rsa -pubout -in jwt-private-key.pem -out jwt-public-key.pem
```

```bash
export JWT_PUBLIC_KEY="$(cat jwt-public-key.pem)"
```

**If `JWT_PUBLIC_KEY` is unset, every authenticated request returns `401` no matter what you do.**
That is the single most common wasted hour. `settings.jwt_public_key` defaults to `""`, and an
empty verification key fails every signature.

Neither `.pem` file is gitignored by name. Delete them when you are done, and never commit one.

## 2. The trap that will cost you an afternoon

All seven services install a top-level package called `app` into one shared virtualenv. The
alphabetically first one wins, which is `analogue`:

```bash
uv run python -c "import app.main; print(app.main.app.title)"
```

From the repo root this prints `analogue`, whatever service you think you are working on.

**Always run from your own service directory:**

```bash
cd services/inventory && uv run uvicorn app.main:app --port 8001
```

Started from the repo root, `uvicorn app.main:app` serves *analogue*. It will answer `/healthz`
perfectly and 404 every route you wrote, which is a maximally confusing failure. This is Defect C
in [`auth-implementation.md`](auth-implementation.md); the same note applies to
`python -m app.anything`.

## 3. Getting a token without running `auth`

This is the recommended loop. `auth` is not on your request path, so it does not need to exist for
you to develop against it.

```python
import uuid

import jwt

HOSPITAL_ID = str(uuid.uuid4())  # any UUID; RLS filters on session_scope, not this literal


def dev_token(role: str = "pharmacist") -> str:
    return jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "hospital_id": HOSPITAL_ID,
            "role": role,
            "aud": "medstock",       # required — verification rejects the token without it
        },
        open("jwt-private-key.pem").read(),
        algorithm="RS256",
    )
```

`exp` is optional for local work; add one when you want to test expiry. Use the token as either a
header or a cookie — your service accepts both:

```bash
curl -s http://127.0.0.1:8001/exposure -H "Authorization: Bearer $TOKEN"
```

```bash
curl -s http://127.0.0.1:8001/exposure -H "Cookie: medstock_token=$TOKEN"
```

The cookie is what the browser sends; the header is for `curl` and tests. Both were verified to
reach the same `Principal`.

## 4. Running the real `auth` instead

Do this when you want to exercise a genuine login, or before a demo. It needs PostgreSQL —
[`auth-implementation.md`](auth-implementation.md) Prerequisites has the install.

```bash
cd services/auth && uv run uvicorn app.main:app --port 8000
```

```bash
TOKEN=$(curl -s -i -X POST http://127.0.0.1:8000/login -H 'content-type: application/json' -d '{"email":"ann@stmarys.org","password":"devpassword123"}' | grep -i '^set-cookie' | sed 's/.*medstock_token=\([^;]*\).*/\1/')
```

Seeded accounts are `ann@` (pharmacist), `ben@` (physician), `cara@` (director), `dan@` (admin), all
`@stmarys.org` — see `services/auth/app/seed.py`.

Your shell and `auth`'s shell must export the **same** `JWT_PUBLIC_KEY`, and `auth` additionally
needs `JWT_PRIVATE_KEY`.

## 5. Protecting a route

```python
from fastapi import Depends, FastAPI

from medstock_shared import Principal, require

app = FastAPI(title="inventory")


@app.get("/exposure")
def exposure(p: Principal = Depends(require("inventory:read"))) -> dict:
    # p.user_id, p.hospital_id, p.role are populated and verified.
    # A bad or missing token was already a 401. A wrong role was already a 403.
    ...
```

`Depends(...)` in an argument default is FastAPI's idiom; ruff's `B008` is configured to allow it in
the root `pyproject.toml`, so you do not need a `noqa`.

Use bare `current_principal` instead of `require(...)` only for a route with no permission check.
That is rare — most routes want one.

### The permission map, as it stands today

| Role | Permissions (abridged — `auth.py` is source of truth) |
|---|---|
| `pharmacist` | `inventory:read`, `queue:read`, `recommendation:approve`, `facility:read`, `forecast:read`, `forecast:run`, `audit:read`, `certificate:read`, `certification:explore`, `profile:*` |
| `physician` | `alert:read`, `inventory:read`, `drug:search`, `facility:read`, `patient:*`, `certificate:read`, `profile:assess`, `profile:explain` |
| `director` | `audit:read`, `dashboard:read`, `inventory:read`, `facility:read`, `forecast:read`, `forecast:run`, `certificate:read`, `profile:review` |
| `admin` | `audit:read`, `formulary:write`, `inventory:read`, `mapping:approve`, `facility:read`, `patient:*`, `certificate:read`, `certification:explore`, `profile:review` |

Verified behaviour of `require("queue:read")`: `pharmacist` → `200`, and `physician`, `director`,
`admin` → `403`. Note that `admin` is **not** a superuser — it holds four specific permissions and
nothing more.

Need a permission that does not exist? Add it to the relevant role's set in
`shared/medstock_shared/auth.py`. That is a `shared/` edit, so it redeploys all seven services —
keep it as its own small PR to avoid conflicting with someone doing the same thing.

## 6. Tenancy — and what is not yet enforced

```python
from medstock_shared import session_scope

with session_scope(p.hospital_id, p.user_id) as s:
    s.query(FormularyItem).all()      # no WHERE hospital_id — do not write one
```

**Row-level security is on.** Wave 2 ENABLE/FORCE RLS on every existing tenant table.
`session_scope()` sets `app.hospital_id`, `app.actor_id`, `app.actor_system`, and
`SET LOCAL ROLE app_role` (docker/CI superuser would otherwise bypass FORCE RLS).
Identity and reference tables stay exempt (`services.md` §1.1).

Write your queries with no manual `WHERE hospital_id` — RLS is the filter. A forgotten
`session_scope` yields zero rows, not every row.

Use `session_scope`, not `engine.connect()`. The raw engine is fine for `/readyz` (`SELECT 1`) and
nothing else.

## 7. Testing your service

Copy this into `services/<yours>/tests/conftest.py`. The first block is the Defect C fix; without
it your tests import analogue's application and pass while testing nothing.

```python
import pathlib
import sys

# MUST come before any `from app...` import. Seven services install a package
# named `app` into one venv and analogue wins the name.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from medstock_shared import settings

HOSPITAL_ID = str(uuid.uuid4())


@pytest.fixture(scope="session")
def token():
    """Returns token(role="pharmacist") -> str. Generates a throwaway keypair
    and points the verifier at it, so tests need no auth process, no database,
    and no .pem files on disk."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    settings.jwt_public_key = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )

    def make(role: str = "pharmacist") -> str:
        return jwt.encode(
            {"sub": str(uuid.uuid4()), "hospital_id": HOSPITAL_ID, "role": role,
             "aud": "medstock"},
            private_pem,
            algorithm="RS256",
        )

    return make
```

Then in a test file — **not** in `conftest.py`, because pytest imports conftest as a plugin and
never collects test functions from it:

```python
from fastapi.testclient import TestClient

from app.main import app


def test_conftest_resolved_the_right_app():
    """If this fails, every other test in this file is testing another service."""
    assert app.title == "inventory"          # your service's name


def test_exposure_requires_a_token():
    assert TestClient(app).get("/exposure").status_code == 401


def test_physician_is_forbidden(token):
    resp = TestClient(app).get("/exposure", headers={"Authorization": f"Bearer {token('physician')}"})
    assert resp.status_code == 403


def test_pharmacist_is_allowed(token):
    resp = TestClient(app).get("/exposure", headers={"Authorization": f"Bearer {token()}"})
    assert resp.status_code == 200
```

Run **per service**, never the whole suite — `uv run pytest -q` at the root aborts during collection
because seven services have an identically named `test_health.py`:

```bash
uv run pytest services/inventory -q
```

## 8. Wiring the web app to your backend

`web/next.config.mjs` proxies `/api/:service/:path*` to `http://localhost:8000` in dev — **all
services to one port**. If you run your backend on 8001, change that `destination` locally while you
work on it. In a cluster this rewrite is never used; the Ingress routes by path to the right pod.

From the browser, call through `apiFetch`, which sends the cookie automatically:

```typescript
import { apiFetch } from "@/lib/api";

const rows = await apiFetch("inventory", "/exposure");
```

Do not read or store a token in front-end code. The cookie is `httpOnly` — no script can read it,
and none needs to.

## 9. When it returns the wrong thing

| Symptom | Cause |
|---|---|
| `401 {"detail":"missing credentials"}` | No `Authorization` header and no `medstock_token` cookie |
| `401 {"detail":"invalid token"}` | `JWT_PUBLIC_KEY` unset or does not match the signing key; missing `aud: "medstock"`; expired `exp` |
| `403 {"detail":"forbidden"}` | Token is valid; the role lacks the permission. Check the table in §5 |
| Your routes all `404`, `/healthz` works | Defect C — you started uvicorn from the repo root and are running analogue (§2) |
| Everything `401` right after a keypair regeneration | Old token signed by the old key. Mint a new one |
| Rows from another hospital appear | Bug — FORCE RLS should hide them. Check `session_scope` ran and the process is `app_role` |

## 10. Do not build these

They are `auth`'s job, or deliberately deferred (`auth-spec.md` §6):

- Your own token verification, claim parsing, or `Principal` type.
- An `is_this_mine?` check on rows — that is RLS's job.
- Audit-log writes — a database trigger handles `review_decision`, not application code.
- Password handling, login screens, session storage, or refresh logic of any kind.
- A permission check written inline in your service instead of added to `PERMS`.
