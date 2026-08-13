# `auth` — service specification & build plan

Owner: **Tymur**. Companion to [services.md](services.md) §1.4, §3, §8 and
[`services/auth/README.md`](../services/auth/README.md) (the consumer-facing contract, already
written and **fixed** — this document specifies the issuing side that makes it true).

Status: **spec** — decisions here are settled unless marked `OPEN`.

---

## 0. Scope

`auth` is the only service that:

- holds the JWT **private** key and mints tokens,
- owns the identity tables (`hospital`, `app_user`, `membership`),
- verifies a password.

It is **not** on anyone's request path (§1.4). Six of the seven other services never call it; the
browser calls it exactly twice per session (login, logout). That is the whole product surface.

Everything the other services need from auth — `Principal`, `require()`, the claim set — already
exists in `shared/medstock_shared/auth.py` and is not being redesigned. This spec only fills the
hole marked *"`auth` itself is unbuilt"* in `services/auth/README.md`.

**Out of scope, stated so nobody waits on it:** password reset, email verification, MFA, SSO/OIDC,
refresh-token rotation, a revocation list, self-service signup, admin user-management UI. §6 says
when each earns its place.

---

## 1. Data model

Three tables, in `shared/medstock_shared/models.py` (Alembic autogenerate reads `Base.metadata`,
so they must live there, not in `services/auth/`).

```
hospital ──1:*── membership ──*:1── app_user
```

### `hospital`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK, `gen_random_uuid()` | This is the value that lands in the `hospital_id` claim and in `app.hospital_id` |
| `name` | `text NOT NULL` | |
| `created_at` | `timestamptz NOT NULL DEFAULT now()` | |

`uuid` rather than `bigserial` deliberately: this id is a claim in a token the browser holds, and
it appears in RLS predicates. Sequential integers there advertise tenant count and make a
guessed-id probe meaningful. The reference tables keep `bigserial` — they are not a tenant boundary.

### `app_user`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | Lands in the `sub` claim and in `app.actor_id` |
| `email` | `citext NOT NULL UNIQUE` | `citext` so `Ann@x.org` and `ann@x.org` cannot be two accounts. Needs `CREATE EXTENSION citext` in the migration |
| `password_hash` | `text NOT NULL` | Argon2id, see §3 |
| `full_name` | `text` | For the review queue's "approved by" line |
| `is_active` | `boolean NOT NULL DEFAULT true` | Deactivation is the only "revocation" that exists; it takes effect on next login, not immediately (§6) |
| `failed_attempts` | `int NOT NULL DEFAULT 0` | §3 |
| `locked_until` | `timestamptz` | §3 |
| `created_at` | `timestamptz NOT NULL DEFAULT now()` | |

Not `user` — reserved word in Postgres, and `app_user` matches `app_role` already used in §1.3.

### `membership`

| Column | Type | Notes |
|---|---|---|
| `user_id` | `uuid` FK → `app_user.id`, `ON DELETE CASCADE` | |
| `hospital_id` | `uuid` FK → `hospital.id` | |
| `role` | `text NOT NULL CHECK (role IN ('pharmacist','physician','director','admin'))` | Must match the keys of `PERMS` in `shared/medstock_shared/auth.py` |
| PK | `(user_id, hospital_id)` | |
| | `UNIQUE (user_id)` | **This is the "one hospital per user" decision, and the only line to drop when it changes** |

This resolves `services.md` §8 open item #4 exactly as it proposed: role lives in `membership` from
day one, so multi-hospital users are a `DROP CONSTRAINT` plus a hospital-picker on login — a
migration, not an auth rewrite. The role is a property of the *membership*, never of the user,
because "director at A, pharmacist at B" is the case that would otherwise force the rewrite.

`CHECK` over an enum type: adding a role to a Postgres enum inside a transaction has version-specific
restrictions, and this constraint has to stay in sync with a Python dict anyway. A check constraint
is one `ALTER` and no type surgery.

### RLS: auth's three tables are exempt, on purpose

Login happens **before** there is a `hospital_id` to set — the whole point of the request is to find
out which hospital the caller belongs to. So:

- `hospital`, `app_user`, `membership` get **no RLS policies** and are queried through `SessionLocal`
  directly, **not** `session_scope()`.
- The safety property is enforced by the queries instead, and there are only three of them: login
  looks up by email and returns claims; `/me` looks up by `principal.user_id`; nothing else reads
  these tables. No endpoint accepts a user id or hospital id from the caller. There is no path
  that can return another user's row because there is no parameter with which to ask for one.
- Password hashes never leave the service. `/me` returns `id`, `email`, `full_name`, `role`,
  `hospital_id`, `hospital_name` — that list is exhaustive and it is a Pydantic response model, so
  a future column cannot leak by accident.

This is the one documented exception to the "always go through `session_scope`" rule in
`services/auth/README.md`. It belongs in this spec rather than as a surprise in a code review.

---

## 2. Endpoints

Base path `/api/auth` (Ingress strips nothing — routes are declared with the prefix, matching the
other services).

### `POST /login`

```jsonc
// request
{ "email": "ann@stmarys.org", "password": "…" }

// 200
{ "user_id": "…", "hospital_id": "…", "role": "pharmacist", "expires_at": "2026-08-13T22:00:00Z" }
// + Set-Cookie: medstock_token=<jwt>; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=28800
```

The token is **not** in the response body. It goes in the cookie only (§4).

Failure is a single `401 {"detail": "invalid credentials"}` for *all* of: unknown email, wrong
password, `is_active = false`, locked account. Distinguishing them turns the endpoint into an
account-existence oracle. Argon2 is verified against a dummy hash even when the email is unknown,
so the response time does not distinguish either.

### `POST /logout`

`204`, `Set-Cookie: medstock_token=; Max-Age=0`. Requires no valid token — logging out with an
expired one must still clear the cookie.

Be honest about what this is: with no revocation list, a token already copied out of the browser
stays valid until it expires. `/logout` clears the browser's copy. That is the entire guarantee,
and it follows from `services.md` §8 open item #6, not from an oversight here.

### `GET /me`

`Depends(current_principal)` — no permission required, every role may read its own row.

```jsonc
{ "user_id": "…", "email": "…", "full_name": "Ann Reyes", "role": "pharmacist",
  "hospital_id": "…", "hospital_name": "St Mary's" }
```

Reads live from `app_user`/`membership` rather than echoing the claims: this is what makes
"deactivated on next request" visible to the web app even though the token is still cryptographically
valid. Returns `401` if the user is gone or inactive.

`/healthz`, `/readyz` — already written, unchanged.

**No user-creation endpoint.** Users are seeded (§5). An admin CRUD surface is real work
(invitations, email, an authz model for who may create whom) for a demo where four accounts exist.
Add it when a second hospital onboards without a developer present.

---

## 3. Passwords

`argon2-cffi` — already a dependency in `services/auth/pyproject.toml`, so nothing new to add.

```python
from argon2 import PasswordHasher
ph = PasswordHasher()          # argon2id, library defaults
```

Library defaults, not hand-tuned parameters. The defaults track the RFC 9106 recommendation and are
maintained by people who follow that; a hand-picked `time_cost` in our config is a number nobody
will revisit. `ph.check_needs_rehash(hash)` on successful login handles the day the defaults move —
three lines, and it is the only migration path a password store gets.

**Lockout.** Ten consecutive failures sets `locked_until = now() + 15 min` and any login attempt
while locked fails without verifying the hash. Reset both columns on success.

```python
# ponytail: counter is per-account, so it stops credential-stuffing one account
# but not spraying one password across many. Add per-IP throttling at the
# Ingress if that shows up in the logs — it does not belong in application code.
```

Argon2 alone is not a rate limit: it costs the attacker ~50 ms per guess, which is thousands of
guesses per hour against one account. The counter is what makes that pointless. This is the one
place in this spec where the lazy version is not enough, and the counter is two columns.

---

## 4. Tokens

| | Decision |
|---|---|
| Algorithm | `RS256` — already `settings.jwt_algorithm`; asymmetric is the reason §1.4 works at all. HS256 would mean shipping the signing secret to all seven verifiers, i.e. seven services able to mint tokens |
| Claims | `sub`, `hospital_id`, `role`, `aud: "medstock"`, `iat`, `exp` — exactly what `current_principal` reads, plus the two the library needs. Nothing else; `email` in a token is a claim that goes stale |
| TTL | **8 hours.** One pharmacist shift. Say the number out loud at defense |
| Private key | `JWT_PRIVATE_KEY` env, from Secret Manager via the k8s Secret already declared in `deploy/k8s/auth.yaml` |
| Public key | Unchanged — `JWT_PUBLIC_KEY` ConfigMap, all eight deployments |
| Rotation | Regenerate the pair, roll the ConfigMap, roll all seven. Everyone re-logs in. Documented as a manual outage-window procedure; `kid`-based dual-key verification is not built |

### Why 8 hours and no refresh token

The alternative — a 15-minute access token plus a refresh token — only pays for itself if the
refresh token is revocable, and revocability means a `session` table, a rotation scheme, reuse
detection, and a DB read on every refresh. That is a revocation list wearing a different hat, and
`services.md` §8 #6 already decided we are not building one for the MVP.

So the honest choice is between "short TTL, user re-logs in mid-shift" and "shift-length TTL, one
login per shift". At a hospital pharmacy workstation the second is both better UX and the same
security posture, because in neither design can we actually revoke anything. 8 hours is the
deliberate number, not the default.

**The upgrade trigger is specific:** the first requirement that an admin action (deactivate a user,
change a role) take effect in under 8 hours. That requirement buys the `session` table, refresh
rotation, and reuse detection together — not one of them alone.

### Cookie, not `localStorage` — and this changes two files outside `auth`

`services.md` §2 already specifies an `httpOnly; Secure; SameSite=Lax` same-origin session cookie.
Two places don't do that yet, and both are one-line fixes rather than a design change:

1. **`shared/medstock_shared/auth.py`** — `current_principal` reads the `Authorization` header only.
   It gains a cookie fallback:

   ```python
   token = header[7:] if header.startswith("Bearer ") else request.cookies.get("medstock_token")
   if not token:
       raise HTTPException(401, "missing credentials")
   ```

   One guard in the shared function, all seven services get it. The header path stays — local dev
   token-minting (`services/auth/README.md`) and `curl` need it, and keeping both costs one
   `or`.

2. **`web/lib/api.ts`** — drop the `localStorage` read, add `credentials: "include"`. Its own
   `ponytail:` comment already names this as the change to make once `/login` exists. Every page
   calls through `apiFetch`, so nothing else in `web/` moves.

`localStorage` is readable by any script on the page; `httpOnly` is not. Same-origin means `Lax`
is sufficient against CSRF for our cross-site cases and no CORS config appears anywhere — that is
the payoff §2 was already banking on.

**This edit lands in `shared/`, so it redeploys all seven services** — the distributed-monolith tax
(§6 of services.md), paid knowingly, once, in its own small PR.

---

## 5. Seeding

`python -m app.seed` in the `auth` image — one hospital, four users (one per role), passwords from
env or generated and printed once. Idempotent (`ON CONFLICT DO NOTHING` on email), so re-running it
against a live database is safe.

Not an Alembic data migration: a migration that inserts users cannot print a generated password and
cannot be re-run selectively. Not a fixture file either — the demo needs these rows in the real
database, not just in tests.

Run manually. Wiring it into the `migrate-job` would put demo accounts into every environment.

---

## 6. What is deliberately not built

| Not built | Build it when |
|---|---|
| Refresh tokens / revocation list | An admin action must take effect in under 8 hours (§4) |
| Password reset | A non-developer needs an account recovered. Needs an email provider — a new external dependency, per `services.md` §8 #7's reasoning about notifications |
| MFA, SSO/OIDC | A real hospital IT department is the buyer. Then it replaces `/login` wholesale, not augments it — which is why building a half-version now is wasted |
| Multi-hospital users | Drop `UNIQUE (user_id)` on `membership`, add a hospital picker to login (§1) |
| Admin user CRUD | Onboarding happens without a developer present (§2) |
| Login events in `audit_log_entry` | The compliance export (`compliance` service) is asked for "who logged in when". Until then, structured logs to Cloud Logging cover the debugging need at zero schema cost. Note the asymmetry: the audit trigger in §1.3 exists because *state changes* must be provable; a login is not a state change |
| `kid`-based key rotation | A second signing key needs to exist at the same time as the first (§4) |

---

## 7. Build plan

Seven steps, in dependency order. Steps 2–6 are one PR each; step 4 is deliberately separate because
it touches `shared/` and blocks the other three developers on a redeploy.

| # | Step | Touches | Done when |
|---|---|---|---|
| 1 | **Models** — `Hospital`, `AppUser`, `Membership` | `shared/medstock_shared/models.py` | `alembic revision --autogenerate` produces a sane diff |
| 2 | **First migration** — `citext` extension, three tables, FKs, the `CHECK` and `UNIQUE` | `migrations/versions/` | `alembic upgrade head` on an empty database, then `downgrade base`, both clean. **This is the repo's first revision** — coordinate with open item #2 (RLS policies), which is the same migration slot |
| 3 | **`/login` + `/me` + `/logout`** — Argon2 hashing, token minting, lockout counter | `services/auth/app/` (`main.py`, `security.py`, `schemas.py`) | §8's checks pass |
| 4 | **Cookie fallback in `current_principal`** | `shared/medstock_shared/auth.py` | Own PR. Existing header-based tests still pass; a cookie-only request authenticates |
| 5 | **Seed script** | `services/auth/app/seed.py` | Running it twice produces the same four users and no error |
| 6 | **Web login page** | `web/app/auth/page.tsx`, `web/lib/api.ts` | Log in in the browser, land on a page that calls a real endpoint, get a `403` from a role that lacks the permission |
| 7 | **Key generation runbook** | `services/auth/README.md` | Someone else can generate a keypair and populate both k8s objects from the doc alone |

Step 3 is the only one with real logic. Steps 1, 2, 5, 7 are mechanical; step 4 is three lines;
step 6 is a form.

### Ordering note

Steps 1–2 unblock nothing else on the team — but step 2 collides with `services.md` §8 open item
#2 (RLS policies, "first migration"). Decide before writing it whether RLS lands in the same
revision or the one after. Recommendation: **same revision**, because the app role's grants
(`REVOKE UPDATE, DELETE ON audit_log_entry`, no `BYPASSRLS`, non-owner) are database-wide setup
that belongs with the schema's birth, not bolted on once tables have rows in them.

---

## 8. Checks

One test file, `services/auth/tests/test_login.py`, following the existing `test_health.py` shape —
`TestClient`, no fixtures framework, no factories.

```python
def test_login_round_trip()          # correct password → cookie set → GET /me returns the role
def test_wrong_password_is_401()     # and body is identical to the unknown-email 401
def test_unknown_email_is_401()
def test_lockout_after_ten_failures()# 11th attempt fails even with the correct password
def test_token_verifies_with_current_principal()
                                     # mint here, verify through shared/auth.py — this is the
                                     # contract between auth and the other six, so it is the one
                                     # test that must not be skipped
```

The last one is the point of the file. The other five test `auth`; that one tests the *system*, and
it is the check that fails if a claim name drifts.

**Setup:** SQLite in-memory won't work — `citext` and `gen_random_uuid()` are Postgres. Run against
the CI Postgres service with `Base.metadata.create_all(engine, tables=[Hospital.__table__,
AppUser.__table__, Membership.__table__])`, creating only auth's three tables. Do not `create_all()`
everything: the reference tables use `JSONB` and belong to migrations, not to this test's setup.

```python
# ponytail: PasswordHasher() at library defaults makes each login test ~50ms.
# Six tests, fine. Drop time_cost in a conftest fixture if the suite gets slow.
```

---

## 9. Open

1. **`OPEN` — dev keypair distribution.** `services/auth/README.md` tells developers to
   `cat path/to/dev-private-key.pem`, and no such file exists or should be committed. Either commit
   a clearly-labelled *dev-only* keypair (fine — it signs nothing real, and it is what makes the
   README's local-dev instructions executable today) or add a `make keys` target each developer runs
   once. Pick one before step 7; committing the dev pair is the lazier answer and the honest risk is
   someone reusing it in a deployed environment, which the k8s Secret already prevents by overriding it.
2. **`OPEN` — `aud` is a constant.** `current_principal` hard-codes `audience="medstock"` and this
   spec mints the same. That is correct and needs no config; recorded here only so nobody "fixes" it
   into a setting.
