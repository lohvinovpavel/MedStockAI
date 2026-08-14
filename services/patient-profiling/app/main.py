"""patient-profiling — substitution safety and cohort demand (PP-1, PP-2).

Accepts a **de-identified feature vector** from the hospital, never a patient
record. Unrecognised fields are dropped rather than stored — that rejection is
what keeps the boundary real (docs/phi-readiness.md §4).

Nothing here calls a model. Same vector in, same verdict out, with the same
reasons — see docs/patient-pipeline.md.
"""

from fastapi import Body, Depends, FastAPI, HTTPException
from medstock_shared.auth import Principal, require
from medstock_shared.db import engine
from medstock_shared.patient import (
    BANDS,
    RULESET_VERSION,
    WEIGHTS,
    PatientVector,
    assess,
    plan_demand,
)
from sqlalchemy import text

app = FastAPI(title="patient-profiling")

MAX_CANDIDATES = 50
MAX_COHORT = 5000


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


@app.get("/ruleset")
def get_ruleset(_: Principal = Depends(require("inventory:read"))) -> dict:
    """The weight table and the bands, published. A tool that will not show you
    how it scored something is a tool a pharmacist is right to distrust."""
    return {
        "version": RULESET_VERSION,
        "weights": WEIGHTS,
        "bands": [{"from_score": t, "verdict": str(v)} for t, v in BANDS],
        "notes": [
            "A hard gate (allergy, duplicate ingredient) blocks and produces no score.",
            "Findings marked info carry weight 0 and record what could not be checked.",
        ],
    }


@app.post("/assess")
def post_assess(
    payload: dict = Body(...),
    _: Principal = Depends(require("inventory:read")),
) -> dict:
    """One patient, one or more candidate drugs.

    Body: `{"patient": {…feature vector…}, "candidates": ["861007", …]}`
    """
    patient = PatientVector.from_json(payload.get("patient") or {})
    candidates = [str(c) for c in (payload.get("candidates") or []) if c]
    if not candidates:
        raise HTTPException(status_code=422, detail="candidates must not be empty")
    if len(candidates) > MAX_CANDIDATES:
        raise HTTPException(status_code=400, detail=f"at most {MAX_CANDIDATES} candidates")

    results = [assess(patient, rxcui).as_dict() for rxcui in candidates]
    return {
        "ruleset_version": RULESET_VERSION,
        "patient_ref": patient.patient_ref,
        "results": results,
    }


@app.post("/demand")
def post_demand(
    payload: dict = Body(...),
    _: Principal = Depends(require("inventory:read")),
) -> dict:
    """Cohort -> purchasing plan.

    Body: `{"cohort": [ …vectors… ], "candidates": [...], "on_hand": {rxcui: units},
            "unavailable": [rxcui], "units_per_patient": 30}`

    `unavailable` is the bridge from COMP-1: a drug that is red or in shortage is
    withdrawn from supply and its patients are re-routed to the option that is
    safest **for each of them**, not to one alternative picked for the cohort.
    """
    raw_cohort = payload.get("cohort") or []
    if len(raw_cohort) > MAX_COHORT:
        raise HTTPException(status_code=400, detail=f"cohort of at most {MAX_COHORT}")
    cohort = [PatientVector.from_json(p) for p in raw_cohort]
    candidates = [str(c) for c in (payload.get("candidates") or []) if c]
    if not cohort or not candidates:
        raise HTTPException(status_code=422, detail="cohort and candidates must not be empty")

    lines, unserved = plan_demand(
        cohort,
        candidates,
        on_hand={str(k): int(v) for k, v in (payload.get("on_hand") or {}).items()},
        unavailable=[str(u) for u in (payload.get("unavailable") or [])],
        units_per_patient=int(payload.get("units_per_patient") or 30),
    )

    return {
        "ruleset_version": RULESET_VERSION,
        "cohort_size": len(cohort),
        "unavailable": [str(u) for u in (payload.get("unavailable") or [])],
        "lines": [
            {
                "rxcui": line.rxcui,
                "on_therapy": line.on_therapy,
                "substitutes_for": line.substitutes_for,
                "eligible": line.eligible,
                "blocked": line.blocked,
                "flagged": line.flagged,
                "units_needed": line.units_needed,
                "on_hand": line.on_hand,
                "shortfall": line.shortfall,
                "block_reasons": line.reasons,
            }
            for line in sorted(lines.values(), key=lambda x: -x.units_needed)
        ],
        "total_shortfall": sum(line.shortfall for line in lines.values()),
        # Patients whose therapy was withdrawn and who have no tolerated in-class
        # alternative. This is the number that decides whether a shortage is a
        # purchasing problem or a clinical one.
        "unservable": unserved,
        "unservable_total": sum(unserved.values()),
    }
