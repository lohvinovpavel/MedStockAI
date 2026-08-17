# A2 — MFA / OTP step

**Service:** `auth` · **Flow:** 1 · **Status:** ❌ not implemented (UI-only) · **Scope:** none (pre-auth)

## Goal

`web/app/login/page.tsx` renders a second screen that asks for a 6-digit code and accepts
anything. Make the challenge real: `POST /login` stops returning a token when the account
requires MFA and returns a challenge instead; the token is issued only once the code verifies.

## API

### `POST /api/auth/login` (modified)

Unchanged request. Two possible 200 bodies — the client branches on `mfa_required`:

```json
{ "mfa_required": true, "challenge_id": "b0f1…", "expires_at": "2026-08-15T14:31:00Z" }
```
```json
{ "mfa_required": false, "access_token": "…", "token_type": "bearer", "user": {} }
```

### `POST /api/auth/login/otp`

```json
{ "challenge_id": "b0f1…", "code": "123456" }
```

200 returns the same `LoginResponse` shape `POST /login` returns today.
`401 invalid_code` · `410 challenge_expired` · `429 too_many_attempts`.

### `POST /api/auth/login/otp/resend`

`{ "challenge_id": "…" }` → `204`. Invalidates the previous code by overwriting `code_hash`.

## Data model

```sql
CREATE TABLE otp_challenge (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  code_hash   text NOT NULL,
  expires_at  timestamptz NOT NULL,
  attempts    int NOT NULL DEFAULT 0,
  resends     int NOT NULL DEFAULT 0,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_otp_challenge_user ON otp_challenge (user_id);
```

No RLS — the same identity-table exception as `app_user` (`docs/services.md` §1.4): the
challenge exists before there is a `hospital_id` to scope by.

## Rules

1. Code is 6 digits from `secrets.randbelow(1_000_000)`, zero-padded. Never returned in a
   response body, never logged above DEBUG.
2. `code_hash` uses the **same hasher as `password_hash`** — reuse the existing helper in
   `services/auth/`; do not introduce a second KDF. Verification is the hasher's own
   constant-time compare.
3. TTL 5 minutes, checked in SQL (`WHERE expires_at > now()`), not in Python.
4. Max 5 attempts per challenge, max 3 resends. Exceeding either deletes the row and returns
   `429`; the client must restart at `POST /login`.
5. Single use — delete the row inside the same transaction that issues the token.
6. A wrong code does **not** increment `app_user.failed_attempts`; credentials already passed.
   Repeated challenge exhaustion does — that is the brute-force signal worth locking on.
7. Delivery channel is out of scope. Ship `MEDSTOCK_OTP_CHANNEL=log` (writes the code to
   stdout at DEBUG) behind one dispatch function so an SMS provider drops in later.
8. Enrolment is not modelled. Until `app_user` has an MFA flag, gate on
   `MEDSTOCK_MFA_REQUIRED` (default `false`) so the demo logins in flow 2 keep working.

## Failure modes

- Unknown or already-consumed `challenge_id` → `401 invalid_code`, same body and comparable
  latency to a wrong code. Do not distinguish; the difference is an oracle.
- Expired-but-present row → `410`, so the UI says "code expired, resend" rather than "wrong code".
- Clock skew: `expires_at` is server-generated from `now()`, never from a client timestamp.

## Acceptance criteria

- [ ] Correct code within TTL returns exactly the body `POST /login` returns today.
- [ ] Wrong code 5× returns `429` and the row is gone.
- [ ] A consumed `challenge_id` cannot be replayed.
- [ ] `MEDSTOCK_MFA_REQUIRED=false` restores single-step login unchanged.
- [ ] A test asserts the plaintext code appears in no response body.

## Out of scope

TOTP/authenticator apps, recovery codes, per-user enrolment UI, real SMS/email delivery.
