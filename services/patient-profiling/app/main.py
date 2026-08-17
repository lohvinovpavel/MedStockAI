"""patient-profiling — substitution safety, cohort demand, and physician patients.

Accepts a **de-identified feature vector** from the hospital for /assess and
/demand. The demo `patient` table is a deliberate PHI exception for the
prescription-cart capstone: CRUD stores name/DOB, and /cart-check maps the row
to a PatientVector before calling assess() (docs/phi-readiness.md posture
unchanged for the rules engine).
"""

import os
import uuid
from datetime import date
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException
from medstock_shared.auth import Principal, require
from medstock_shared.db import engine, session_scope
from medstock_shared.models import DrugRiskProfile, Patient
from medstock_shared.patient import (
    BANDS,
    RULESET_VERSION,
    WEIGHTS,
    PatientVector,
    RiskProfile,
    assess,
    avoided_ingredient_warnings,
    patient_row_to_vector,
    plan_demand,
    profile_avoided_ingredients,
)
from medstock_shared.rxnorm import RxNormError, ingredients_for_rxcui
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session

app = FastAPI(title="patient-profiling")
patients = APIRouter()

MAX_CANDIDATES = 50
MAX_COHORT = 5000
MAX_CART_ITEMS = 40
BLOOD_GROUPS = {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", "unknown"}


class PatientCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    date_of_birth: date
    blood_group: str | None = None
    allergy_codes: list[str] = Field(default_factory=list)
    condition_codes: list[str] = Field(default_factory=list)


class PatientUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    date_of_birth: date | None = None
    blood_group: str | None = None
    allergy_codes: list[str] | None = None
    condition_codes: list[str] | None = None


class CartItem(BaseModel):
    rxcui: str = Field(min_length=1, max_length=32)
    name: str | None = None


class CartCheckBody(BaseModel):
    patient_id: uuid.UUID
    items: list[CartItem] = Field(default_factory=list)


def _norm_codes(codes: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in codes or []:
        code = str(raw).strip().lower()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _norm_blood(value: str | None) -> str | None:
    if value is None or value.strip() == "":
        return None
    bg = value.strip().upper().replace(" ", "")
    # Accept "A positive"-style loosely by normalizing common forms.
    aliases = {"A+": "A+", "A-": "A-", "B+": "B+", "B-": "B-", "AB+": "AB+", "AB-": "AB-",
               "O+": "O+", "O-": "O-", "UNKNOWN": "unknown"}
    if bg not in aliases and bg.lower() == "unknown":
        return "unknown"
    if bg not in BLOOD_GROUPS and bg not in aliases:
        raise HTTPException(status_code=422, detail=f"blood_group must be one of {sorted(BLOOD_GROUPS)}")
    return aliases.get(bg, bg)


def _patient_dict(row: Patient) -> dict:
    return {
        "id": str(row.id),
        "hospital_id": row.hospital_id,
        "full_name": row.full_name,
        "date_of_birth": row.date_of_birth.isoformat(),
        "blood_group": row.blood_group,
        "allergy_codes": list(row.allergy_codes or []),
        "condition_codes": list(row.condition_codes or []),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _finding_dict(f) -> dict:
    return {
        "code": f.code,
        "severity": str(f.severity),
        "weight": f.weight,
        "message": f.message,
        "source": f.source,
        "stage": f.stage,
    }


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


@app.get("/version")
def version() -> dict[str, str]:
    """GIT_SHA is baked in at image build time (Dockerfile) — unset outside
    a built container, e.g. running locally from source. semver comes from
    the installed medstock-patient-profiling package (pyproject.toml), not the image."""
    try:
        semver = pkg_version("medstock-patient-profiling")
    except PackageNotFoundError:
        semver = "unknown"
    return {
        "service": "patient-profiling",
        "version": os.environ.get("GIT_SHA", "unknown"),
        "semver": semver,
    }


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


def approved_profiles(rxcuis: list[str]) -> list[RiskProfile]:
    """Label-derived risk profiles for these drugs — **approved ones only**.

    The filter is the point. An extracted profile is a model's reading of a
    label until a pharmacist accepts it, and an unapproved one reaching a screen
    is the single failure this design cannot tolerate
    (docs/prognosis-and-procurement.md §1.3).

    A missing table means the prognosis migration has not run; that degrades to
    "no profiles" rather than failing the assessment, because the deterministic
    stages are still perfectly valid without it.
    """
    if not rxcuis:
        return []
    try:
        with Session(engine) as session:
            rows = session.execute(
                select(DrugRiskProfile).where(
                    DrugRiskProfile.rxcui.in_(rxcuis),
                    DrugRiskProfile.status == "approved",
                )
            ).scalars().all()
    except (ProgrammingError, SQLAlchemyError):
        return []
    return [
        RiskProfile(
            rxcui=str(r.rxcui),
            reaction=r.reaction,
            seriousness=r.seriousness,
            risk_factors=tuple(r.risk_factors or ()),
            citation=r.citation or "",
            section=r.section or "",
        )
        for r in rows
    ]


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

    profiles = approved_profiles(candidates)
    results = [assess(patient, rxcui, risk_profiles=profiles).as_dict() for rxcui in candidates]
    return {
        "ruleset_version": RULESET_VERSION,
        "patient_ref": patient.patient_ref,
        # So a caller can tell "no label risk applies to this patient" apart from
        # "nobody has approved a profile for this drug yet".
        "risk_profiles_applied": len(profiles),
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


@patients.get("/patients")
def list_patients(principal: Principal = Depends(require("patient:read"))) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        rows = session.scalars(
            select(Patient)
            .where(Patient.hospital_id == principal.hospital_id)
            .order_by(Patient.full_name)
        ).all()
        return {"items": [_patient_dict(r) for r in rows]}


@patients.post("/patients", status_code=201)
def create_patient(
    body: PatientCreate,
    principal: Principal = Depends(require("patient:write")),
) -> dict:
    blood = _norm_blood(body.blood_group)
    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = Patient(
            hospital_id=principal.hospital_id,
            full_name=body.full_name.strip(),
            date_of_birth=body.date_of_birth,
            blood_group=blood,
            allergy_codes=_norm_codes(body.allergy_codes),
            condition_codes=_norm_codes(body.condition_codes),
        )
        session.add(row)
        session.flush()
        return _patient_dict(row)


@patients.get("/patients/{patient_id}")
def get_patient(
    patient_id: uuid.UUID,
    principal: Principal = Depends(require("patient:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = session.get(Patient, patient_id)
        if row is None or row.hospital_id != principal.hospital_id:
            raise HTTPException(status_code=404, detail="patient not found")
        return _patient_dict(row)


@patients.patch("/patients/{patient_id}")
def update_patient(
    patient_id: uuid.UUID,
    body: PatientUpdate,
    principal: Principal = Depends(require("patient:write")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = session.get(Patient, patient_id)
        if row is None or row.hospital_id != principal.hospital_id:
            raise HTTPException(status_code=404, detail="patient not found")
        if body.full_name is not None:
            row.full_name = body.full_name.strip()
        if body.date_of_birth is not None:
            row.date_of_birth = body.date_of_birth
        if body.blood_group is not None:
            row.blood_group = _norm_blood(body.blood_group)
        if body.allergy_codes is not None:
            row.allergy_codes = _norm_codes(body.allergy_codes)
        if body.condition_codes is not None:
            row.condition_codes = _norm_codes(body.condition_codes)
        session.flush()
        return _patient_dict(row)


@patients.post("/cart-check")
def cart_check(
    body: CartCheckBody,
    principal: Principal = Depends(require("patient:read")),
) -> dict:
    """Physician cart: profile assess + RxNorm ingredient avoid-warnings.

    Warnings only — findings from assess and avoided ingredients are returned
    as `warnings` so the UI can badge lines without hard-blocking prescribe.
    """
    if len(body.items) > MAX_CART_ITEMS:
        raise HTTPException(status_code=400, detail=f"at most {MAX_CART_ITEMS} cart items")

    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = session.get(Patient, body.patient_id)
        if row is None or row.hospital_id != principal.hospital_id:
            raise HTTPException(status_code=404, detail="patient not found")
        vector = patient_row_to_vector(row)
        patient_payload = _patient_dict(row)

    avoided = profile_avoided_ingredients(vector)
    results: list[dict] = []
    for item in body.items:
        rxcui = item.rxcui.strip()
        assessment = assess(vector, rxcui)
        # Surface all findings as warnings for the cart (demo: no hard block UI).
        warnings = [_finding_dict(f) for f in assessment.findings]

        exclude_ingredient: str | None = None
        exclude_ingredient_name: str | None = None
        try:
            ingredients = ingredients_for_rxcui(rxcui)
        except RxNormError:
            ingredients = []
            warnings.append(
                {
                    "code": "RXNORM_UNAVAILABLE",
                    "severity": "info",
                    "weight": 0,
                    "message": "RxNorm unavailable — ingredient check skipped",
                    "source": "rxnorm",
                    "stage": 4,
                }
            )

        for finding in avoided_ingredient_warnings(vector, rxcui, ingredients):
            warnings.append(_finding_dict(finding))
            # Prefer the first avoided ingredient hit for analogue filtering.
            if exclude_ingredient is None:
                for _code, ing_rxcui, ing_name in avoided:
                    if any(
                        str(i["rxcui"]) == ing_rxcui
                        or ing_name.lower() in str(i.get("name") or "").lower()
                        for i in ingredients
                    ):
                        exclude_ingredient = ing_rxcui
                        exclude_ingredient_name = ing_name
                        break

        results.append(
            {
                "rxcui": rxcui,
                "name": item.name,
                "verdict": str(assessment.verdict),
                "score": assessment.score,
                "warnings": warnings,
                "exclude_ingredient": exclude_ingredient,
                "exclude_ingredient_name": exclude_ingredient_name,
                "ingredients": ingredients,
            }
        )

    return {
        "ruleset_version": RULESET_VERSION,
        "patient": patient_payload,
        "results": results,
    }


app.include_router(patients)
app.include_router(patients, prefix="/api/patients")
