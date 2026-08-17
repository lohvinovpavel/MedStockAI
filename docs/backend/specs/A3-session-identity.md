# A3 — Session identity

**Service:** `auth` · **Flows:** 2, 3 · **Status:** ✅ implemented

`GET /me` in `services/auth/app/main.py`, returning `MeResponse`. Reads `app_user` and
`membership`.

**No implementation work.** One gap worth closing when A4 lands:

- [ ] Return the caller's **granted scopes**, not just the role, so the web client can hide
      actions it cannot perform instead of rendering them and catching a 403. `PERMS[role]` is
      already the answer; it just is not serialized.
- [ ] Flow 2 lets the user pick a role that gates nothing in the UI. Once scopes are on `/me`,
      `SideNav` and the inventory row menu should read them — that is a web-side change tracked
      against flow 2, not an `auth` change.
