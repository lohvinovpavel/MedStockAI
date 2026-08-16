# A1 — Login and token issue

**Service:** `auth` · **Flows:** 1, 2 · **Status:** ✅ implemented

Implemented in `services/auth/app/main.py` (`POST /login`, `POST /logout`) with request and
response models in `services/auth/app/schemas.py`. Reads `app_user`, `membership`, `hospital`
through `SessionLocal` directly — the documented identity-table exception (`docs/services.md`
§1.4), because login runs before there is a `hospital_id` to scope by.

**No implementation work.** What remains is verification and two follow-ups owned elsewhere:

- [ ] Confirm `failed_attempts` / `locked_until` actually lock and that the lock expires.
- [ ] Confirm the JWT carries `hospital_id` and `role`, since A4's `session_scope` and every
      other service's local verification depend on both claims being present.
- [ ] A2 changes this endpoint's response shape (adds the `mfa_required` branch). Whoever
      implements A2 owns keeping the existing single-step body byte-identical when MFA is off.
- [ ] Flow 2's demo logins bypass password verification in the web layer only; confirm the
      service itself has no bypass path.

**Do not** add refresh-token rotation, password reset, or SSO without a request — none is in
any flow.
