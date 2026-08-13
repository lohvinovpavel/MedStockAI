# `auth` — how to use it from your service

Owner: **Tymur**. Read this before writing any route in your service.

---

## The one rule

**You never call `auth` over the network.** Every other service verifies the
JWT locally, with a public key it already holds. If you find yourself adding
an HTTP call to `auth` on a request path, stop — that turns `auth` into a
single point of failure for all seven other services.

---

## The one dependency you write

```python
from medstock_shared import Principal, current_principal, require

@app.get("/exposure")
def exposure(p: Principal = Depends(require("inventory:read"))):
    ...
```

`require(permission)` does three things before your handler runs: verifies
the JWT signature, checks `permission` against the caller's role, and gives
you back a `Principal(user_id, hospital_id, role)`. A bad or missing token is
a `401` you never write. A role without the permission is a `403` you never
write.

Use bare `current_principal` instead of `require(...)` only for routes with
no permission check (rare — most routes want one).

---

## Tenancy: always through `session_scope`, never raw `engine`

```python
from medstock_shared import session_scope

with session_scope(p.hospital_id, p.user_id) as s:
    s.query(FormularyItem).all()   # no WHERE hospital_id — don't write one
```

`session_scope` does `SET LOCAL app.hospital_id` / `app.actor_id` for the
transaction. Row-level security does the filtering; a row belonging to
another hospital simply is not there. There is no "is this mine?" check to
forget — and none to add, either.

**Do not go around it.** `engine.connect()` directly is fine for `/readyz`
(`SELECT 1`, no tenant data touched) and nothing else. A real query against a
tenant table over the raw engine skips RLS's tenant context entirely — the
policy machinery is unaffected either way, but your query now runs as
whatever role the connection defaults to, without the `app.hospital_id`
scoping other tenants' rows are hidden behind.

---

## Adding a permission

Roles and their permissions live in one place,
[`shared/medstock_shared/auth.py`](../../shared/medstock_shared/auth.py):

```python
PERMS: dict[str, set[str]] = {
    "pharmacist": {"queue:read", "recommendation:approve", "inventory:read"},
    ...
}
```

If your endpoint needs a permission that doesn't exist yet, add it to the
relevant role's set here — not a local check in your own service. This file
is shared across all seven services, so keep permission-map edits their own
small PR to avoid merge conflicts with someone else doing the same thing.

---

## Calling Gemini? You don't need anything from `auth` for that

```python
from medstock_shared import ask_ai, AIError

try:
    result = ask_ai("analogue", {"rxcui": rxcui, "candidates": candidates, ...})
except AIError:
    raise HTTPException(503, "recommendation unavailable")
```

`ask_ai()` is a plain function call — no token, no `hospital_id` argument.
There is no `ai-handler` service anymore; `ask_ai()` calls Gemini directly
and caches the answer in a shared, **non-tenant** table (`ai_cache` — no
RLS, no `hospital_id` column). That's deliberate: the payload is reference
data (drug names, RxCUI, shortage text), never PHI, so two hospitals asking
the identical question share the identical cached answer on purpose. If a
task you're adding ever needs to pass something hospital-specific into the
prompt, that is a sign it does not belong in this cache — flag it, don't
just wire it through.

---

## What you get for free, and don't need to write

- JWT signature verification
- Row-level tenant isolation (once RLS policies exist — see *Known gaps*)
- RBAC (`403` on the wrong role)
- Audit logging — a DB trigger on `review_decision`, not application code

None of this lives in your service. That's deliberate: the alternative is
seven copies of the same auth logic, one per service, silently drifting.

---

## Claims your token carries

| Claim | Meaning |
|---|---|
| `sub` | user id |
| `hospital_id` | which organization — the tenant boundary |
| `role` | `pharmacist` \| `physician` \| `director` \| `admin` — the RBAC boundary |
| `aud` | must be `"medstock"`, or verification rejects the token |

Two separate axes, don't conflate them: **`hospital_id` separates
organizations** (enforced by the database, via RLS). **`role` separates
users within one organization** (enforced by your service, via `require`).
There is currently no per-row ownership within a hospital — a pharmacist
sees the whole review queue for their hospital, not just their own actions.

---

## Local development

```bash
export JWT_PUBLIC_KEY="$(cat path/to/dev-public-key.pem)"
uv run --package medstock-<yourservice> uvicorn app.main:app --reload --port 8002
```

Mint yourself a token for local testing:

```python
import jwt
token = jwt.encode(
    {"sub": "dev-user", "hospital_id": "<uuid>", "role": "pharmacist", "aud": "medstock"},
    open("path/to/dev-private-key.pem").read(),
    algorithm="RS256",
)
```

---

## Known gaps — don't design around these being solved

- **`auth` itself is unbuilt.** `services/auth/app/main.py` is currently
  `/healthz` + `/readyz` only — no `/login`, no user/hospital tables. The
  contract above (claims, verification) is fixed; the issuing side isn't
  written yet.
- **RLS policies don't exist yet** (`docs/services.md` §8, open item #2).
  `session_scope()` sets `app.hospital_id`, but until `CREATE POLICY` lands,
  nothing reads it — tenant isolation is not actually enforced today, even
  though the code above is already the right shape for when it is.
- **No token revocation.** A role or hospital change takes effect only when
  the old token expires. Don't build a feature that assumes an admin action
  takes effect immediately for an already-logged-in user.
