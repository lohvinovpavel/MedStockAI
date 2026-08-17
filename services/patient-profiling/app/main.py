"""patient-profiling — substitution safety, cohort demand, and physician patients.

Accepts a **de-identified feature vector** from the hospital for /assess and
/demand. The demo `patient` table is a deliberate PHI exception for the
prescription-cart capstone: CRUD stores name/DOB, and /cart-check maps the row
to a PatientVector before calling assess() (docs/phi-readiness.md posture
unchanged for the rules engine).
"""

import os
import uuid
from datetime import UTC, date, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException
from medstock_shared.auth import Principal, require
from medstock_shared.db import engine, session_scope
from medstock_shared.models import AssessmentLog, DrugRiskProfile, Patient, PrognosisAssumption
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
from sqlalchemy import func, select, text
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


REVIEW_ACTIONS = {"approve": "approved", "reject": "rejected"}
PROFILE_STATUSES = ("awaiting_approval", "approved", "rejected")
MAX_QUEUE = 200


class ReviewBody(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    note: str = Field(default="", max_length=2000)


def review_update(action: str, actor: str, note: str, now: datetime) -> dict:
    """The columns a ruling writes. Pure, so the rule that a rejection records
    its reviewer just as an approval does is testable without a database."""
    return {
        "status": REVIEW_ACTIONS[action],
        "reviewed_by": actor,
        "reviewed_at": now,
        "review_note": note.strip(),
    }


def _profile_dict(row: DrugRiskProfile) -> dict:
    return {
        "id": row.id,
        "rxcui": str(row.rxcui),
        "reaction": row.reaction,
        "seriousness": row.seriousness,
        # The reviewable basis. A queue that showed a verdict without the
        # factors and the quote would be asking for a signature on nothing.
        "risk_factors": list(row.risk_factors or []),
        "citation": row.citation or "",
        "section": row.section or "",
        "spl_id": row.spl_id,
        "status": row.status,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "review_note": row.review_note or "",
        "extracted_at": row.extracted_at.isoformat() if row.extracted_at else None,
    }


def accept_rate(counts: dict[str, int]) -> float | None:
    """Approved as a share of everything ruled on — docs §5.4's number, the one
    that says whether extraction is good enough to trust.

    Profiles still awaiting review are excluded on purpose. Counting them as
    failures would make the rate start at zero and climb as reviewing happens,
    which measures the reviewer's progress rather than the model's accuracy.
    `None` while nothing has been ruled on, because a rate over no decisions is
    not 0.0 — it is unknown, and the two must not print the same.
    """
    ruled = counts.get("approved", 0) + counts.get("rejected", 0)
    return round(counts.get("approved", 0) / ruled, 3) if ruled else None


def load_queue(status: str, rxcui: str | None, limit: int) -> tuple[list[dict], dict[str, int]]:
    """The queue and the tally behind it. Separated from the endpoint so the
    response shape can be tested without a Postgres to point at."""
    with Session(engine) as session:
        query = select(DrugRiskProfile)
        if status != "all":
            query = query.where(DrugRiskProfile.status == status)
        if rxcui:
            query = query.where(DrugRiskProfile.rxcui == str(rxcui))
        # Oldest first: a queue that shows the newest extraction first leaves
        # the backlog sitting at the bottom for ever.
        rows = session.scalars(query.order_by(DrugRiskProfile.extracted_at).limit(limit)).all()
        # Over the whole table, not the page — this is what the accept rate is
        # computed from, and a rate over one page of 50 is not the accept rate.
        tally = session.execute(
            select(DrugRiskProfile.status, func.count()).group_by(DrugRiskProfile.status)
        ).all()
    counts = {s: 0 for s in PROFILE_STATUSES} | {str(s): int(n) for s, n in tally}
    return [_profile_dict(r) for r in rows], counts


def apply_review(profile_id: int, updates: dict) -> tuple[str, dict] | None:
    """Write a ruling. Returns (status before, the row after), or None if there
    is no such profile."""
    with Session(engine) as session:
        row = session.get(DrugRiskProfile, profile_id)
        if row is None:
            return None
        previous = row.status
        for column, value in updates.items():
            setattr(row, column, value)
        session.commit()
        session.refresh(row)
        return previous, _profile_dict(row)


@patients.get("/risk-profiles")
def list_risk_profiles(
    status: str = "awaiting_approval",
    rxcui: str | None = None,
    limit: int = 50,
    _: Principal = Depends(require("profile:review")),
) -> dict:
    """The review queue: what a model has proposed and nobody has ruled on yet.

    `status=all` shows every profile, which is what makes an approval auditable
    after the fact rather than only actionable before it.

    Unlike `approved_profiles`, a missing table is a **503 here, not an empty
    list**. On the request path an absent migration should degrade to "no
    prognosis" and let the deterministic stages answer. On this path an empty
    list reads as "nothing to review" — a reviewer would close the page and the
    backlog would be invisible — so the failure has to be loud.
    """
    if status != "all" and status not in PROFILE_STATUSES:
        raise HTTPException(
            status_code=422, detail=f"status must be 'all' or one of {PROFILE_STATUSES}"
        )
    limit = max(1, min(int(limit), MAX_QUEUE))

    try:
        items, counts = load_queue(status, rxcui, limit)
    except (ProgrammingError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="risk profile table unavailable") from exc

    return {
        "status": status,
        "limit": limit,
        "items": items,
        "counts": counts,
        "accept_rate": accept_rate(counts),
    }


@patients.post("/risk-profiles/{profile_id}/review")
def review_risk_profile(
    profile_id: int,
    body: ReviewBody,
    principal: Principal = Depends(require("profile:approve")),
) -> dict:
    """Gate 3 of docs/prognosis-and-procurement.md §1.3, and the only way a
    profile ever reaches a screen.

    Re-ruling on a profile that was already decided is allowed, and deliberately
    so: a label changes, or an approval turns out to have been wrong, and
    withdrawing it must not require a database edit. The status it had before
    comes back in the response, so an approval being overturned is visible
    rather than inferred.
    """
    updates = review_update(body.action, principal.user_id, body.note, datetime.now(UTC))
    try:
        ruled = apply_review(profile_id, updates)
    except (ProgrammingError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="risk profile table unavailable") from exc
    if ruled is None:
        raise HTTPException(status_code=404, detail="risk profile not found")

    previous, profile = ruled
    return {"previous_status": previous, "profile": profile}


def record_assessment(
    principal: Principal, vector: PatientVector, results: list[dict]
) -> str:
    """Write the decision trail row, and return the request id.

    **Fails the request if it cannot write.** An assessment that reaches a
    clinician without a corresponding audit row is exactly the hole
    docs/services.md §1.3 claims does not exist — and it is a silent hole, since
    the answer looks identical either way. Answering unaudited is the worse
    failure of the two, so this refuses rather than degrades.

    Note the contrast with `approved_profiles`, which swallows a missing table:
    that one degrades a *feature*, this one would falsify a *guarantee*.
    """
    request_id = str(uuid.uuid4())
    with session_scope(principal.hospital_id, principal.user_id) as session:
        session.add(
            AssessmentLog(
                hospital_id=principal.hospital_id,
                request_id=request_id,
                actor_id=principal.user_id,
                feature_hash=vector.feature_hash(),
                ruleset_version=RULESET_VERSION,
                # The verdict and why, not the whole finding text — enough to
                # reconstruct what the clinician was shown.
                result={
                    "assessments": [
                        {
                            "rxcui": r.get("rxcui"),
                            "verdict": r.get("verdict"),
                            "score": r.get("score"),
                            "codes": [f.get("code") for f in (r.get("findings") or [])],
                        }
                        for r in results
                    ]
                },
            )
        )
    return request_id


@app.post("/assess")
def post_assess(
    payload: dict = Body(...),
    principal: Principal = Depends(require("inventory:read")),
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
    request_id = record_assessment(principal, patient, results)
    return {
        "ruleset_version": RULESET_VERSION,
        # Quote this back to dispute an answer; it is the key into assessment_log.
        "request_id": request_id,
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


DEFAULT_SWITCH_RATE = 0.6


def assumption(name: str, fallback: float) -> tuple[float, str]:
    """One PP-4 assumption and the note explaining it.

    Falls back rather than failing if the table is missing, for the same reason
    approved_profiles does: the migration not having run is a deployment state,
    not a reason a director cannot see a forecast. The fallback matches the
    value the migration seeds, so a degraded read gives the same number rather
    than a quietly different one.
    """
    try:
        with Session(engine) as session:
            row = session.execute(
                select(PrognosisAssumption).where(PrognosisAssumption.name == name)
            ).scalar_one_or_none()
    except (ProgrammingError, SQLAlchemyError):
        return fallback, "assumption table unavailable — using the built-in default"
    if row is None:
        return fallback, "no row for this assumption — using the built-in default"
    return float(row.value), str(row.note or "")


@patients.post("/forecast")
def forecast(
    payload: dict = Body(...),
    _: Principal = Depends(require("inventory:read")),
) -> dict:
    """Cohort -> purchasing plan, plus where the therapy is heading (PP-4).

    Body: the `/plan` body, plus optional `switch_rate` and `horizon_days`.

    `/plan` answers *what are they on, and what is safe*. This adds *and where
    is that going*: two hospitals with the same headcount and the same current
    prescriptions still need different stock, because their patients differ
    (docs/prognosis-and-procurement.md §2.1).

    Every projected number here rests on `switch_rate`, which is assumed rather
    than measured, so the response echoes it back under `assumptions`. A
    forecast that does not say which of its inputs were chosen rather than
    observed is the one dishonest thing this design could do.
    """
    raw_cohort = payload.get("cohort") or []
    if len(raw_cohort) > MAX_COHORT:
        raise HTTPException(status_code=400, detail=f"cohort of at most {MAX_COHORT}")
    cohort = [PatientVector.from_json(p) for p in raw_cohort]
    candidates = [str(c) for c in (payload.get("candidates") or []) if c]
    if not cohort or not candidates:
        raise HTTPException(status_code=422, detail="cohort and candidates must not be empty")

    seeded_rate, note = assumption("switch_rate", DEFAULT_SWITCH_RATE)
    override = payload.get("switch_rate")
    if override is None:
        switch_rate, rate_source = seeded_rate, "prognosis_assumption"
    else:
        try:
            switch_rate = float(override)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="switch_rate must be a number") from None
        if not 0.0 <= switch_rate <= 1.0:
            raise HTTPException(status_code=422, detail="switch_rate must be between 0 and 1")
        rate_source, note = "request", "supplied per-request, overriding the stored assumption"

    profiles = approved_profiles(candidates)
    unavailable = [str(u) for u in (payload.get("unavailable") or [])]
    lines, unserved = plan_demand(
        cohort,
        candidates,
        on_hand={str(k): int(v) for k, v in (payload.get("on_hand") or {}).items()},
        unavailable=unavailable,
        units_per_patient=int(payload.get("units_per_patient") or 30),
        risk_profiles=profiles,
    )

    cohort_size = len(cohort)
    return {
        "ruleset_version": RULESET_VERSION,
        "cohort_size": cohort_size,
        "horizon_days": int(payload.get("horizon_days") or 90),
        "unavailable": unavailable,
        "lines": [
            {
                "rxcui": line.rxcui,
                "on_therapy": line.on_therapy,
                "substitutes_for": line.substitutes_for,
                "eligible": line.eligible,
                "blocked": line.blocked,
                "flagged": line.flagged,
                "at_risk": line.at_risk,
                "switch_in": line.switch_in,
                "cohort_fit": line.cohort_fit(cohort_size),
                "projected_patients": line.projected(switch_rate),
                "projected_units": line.projected(switch_rate)
                * int(payload.get("units_per_patient") or 30),
                "units_needed": line.units_needed,
                "on_hand": line.on_hand,
                "shortfall": line.shortfall,
                "block_reasons": line.reasons,
            }
            for line in sorted(lines.values(), key=lambda x: -x.units_needed)
        ],
        "total_shortfall": sum(line.shortfall for line in lines.values()),
        "unservable": unserved,
        "unservable_total": sum(unserved.values()),
        "risk_profiles_applied": len(profiles),
        # Everything below was chosen, not derived. Named so a reader can tell
        # which parts of the forecast are observation and which are assumption.
        "assumptions": {
            "switch_rate": {"value": switch_rate, "source": rate_source, "note": note},
        },
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
    # One lookup for the whole cart, not one per line: approved_profiles takes a
    # list precisely so a ten-item cart costs a single query.
    cart_rxcuis = [item.rxcui.strip() for item in body.items if item.rxcui.strip()]
    profiles = approved_profiles(cart_rxcuis)
    results: list[dict] = []
    for item in body.items:
        rxcui = item.rxcui.strip()
        assessment = assess(vector, rxcui, risk_profiles=profiles)
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

    # Logged for the same reason /assess is: this produces a per-patient verdict
    # a physician acts on. The cohort endpoints (/demand, /forecast) are not
    # logged here — they answer a purchasing question about a group, not a
    # clinical decision about a person, and assessment_log is the clinical trail.
    request_id = record_assessment(principal, vector, results)

    return {
        "ruleset_version": RULESET_VERSION,
        "request_id": request_id,
        "patient": patient_payload,
        "results": results,
        # Mirrors /assess. Zero here is meaningful: it distinguishes "no approved
        # profile covers this cart" from "the prognosis stage never ran", which
        # otherwise look identical from a response with no PP-3 findings in it.
        "risk_profiles_applied": len(profiles),
    }


app.include_router(patients)
app.include_router(patients, prefix="/api/patients")
