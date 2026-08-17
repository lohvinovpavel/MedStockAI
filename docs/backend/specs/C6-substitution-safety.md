# C6 — Substitution safety check

**Service:** `patient-profiling` · **Flow:** 7 · **Status:** ✅ implemented

`POST /assess`, `POST /demand`, and `GET /ruleset` in `services/patient-profiling/app/main.py`.

**Nothing here calls a model, and that is the design.** Same vector in, same verdict out, with
the same reasons. `GET /ruleset` publishes the `WEIGHTS` and `BANDS` that produced the score —
a tool that will not show you how it scored something is a tool a pharmacist is right to
distrust.

For a substitution-safety call made under an ISO-13485 claim, a deterministic weighted ruleset
is the correct answer, not a limitation to be upgraded later. Keep it.

**No implementation work.** Open items owned elsewhere:

- [ ] `docs/services.md` §3 marks PHI storage `OPEN`. The MVP stores none; that is a defensible
      answer and removes a compliance argument. Decide explicitly before any schema work here.
- [ ] Flow 7 does not currently call this endpoint before showing analogues. Wiring it in is a
      web-side change: an analogue list that has not been safety-checked should say so.

**Do not** register a `patient-profiling` task in `ai_tasks.py`. The commented placeholder there
is not an invitation.
