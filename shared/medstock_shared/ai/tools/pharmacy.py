"""Copilot tools -- docs/ai_workflow_impl_plan.md.

Each is a thin wrapper around a read `medstock_shared` already implements, or
a plain query against a reference/tenant table -- never new business logic.
`check_bioequivalence`, named in the original brief, is deliberately not
here -- there is no bioequivalence data or logic anywhere in this repo to
wrap. Declaring it to Gemini with nothing behind it would make it a
hallucination generator, exactly the failure mode docs/ai-module-plan.md
Phase 4 rules out for
`generate_draft_po`/`approve_and_send_po`/`approve_emergency_protocol`.
Add it here, for real, once that logic exists somewhere.
"""

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...ai_audit import query_ai_decisions as _query_ai_decisions
from ...auth import Principal
from ...certification import RULES, Finding, ndc11, signal, signal_for_ndc
from ...db import engine, session_scope
from ...explore import explore
from ...forecasting import HORIZON_DAYS
from ...forecasting import at_risk_skus as _at_risk_skus
from ...forecasting import latest_run as _latest_run
from ...models import (
    CertificationFinding,
    Drug,
    DrugCertification,
    Facility,
    ForecastPoint,
    LocationCondition,
    Patient,
    StockSnapshot,
    StorageLocation,
)
from ...ordering import create_purchase_order
from ...patient import age_band_from_dob
from ...patient_assess import NOT_FOUND, UNAVAILABLE, resolve_patient_ref
from ...patient_assess import assess_for_drug as _assess_for_drug
from ...patient_assess import explain_assessment as _explain_assessment
from ...review_queue import PROFILE_STATUSES, accept_rate
from ...review_queue import load_queue as _load_queue
from ...rxnorm import RxNormError, ndcs_for_rxcui, related_scd_sbd, therapeutic_scd_sbd
from ...stock import stock_fields
from ...warehouse import excursions
from .registry import tool

_KEEP_LIMIT = 5


def _stock_totals(principal: Principal, ndcs: list[str]) -> dict[str, int]:
    """Hospital on-hand per NDC. ponytail: duplicates
    services/analogue/app/main.py's stock_totals_by_ndc (~10 lines) rather
    than promoting it to shared for one more caller -- promote it if a third
    caller needs the same query."""
    unique = list(dict.fromkeys(ndcs))
    if not unique:
        return {}
    with session_scope(principal.hospital_id, principal.user_id) as session:
        rows = session.execute(
            select(StockSnapshot.ndc, func.coalesce(func.sum(StockSnapshot.quantity), 0))
            .where(StockSnapshot.ndc.in_(unique))
            .group_by(StockSnapshot.ndc)
        ).all()
        return {str(ndc): int(qty or 0) for ndc, qty in rows}


class FindDrugArgs(BaseModel):
    name: str = Field(description="Drug name or fragment as the user typed it, e.g. 'propofol'")
    stocked_only: bool = Field(True, description="Restrict to drugs this hospital stocks")


@tool(
    permission="drug:search",
    description=(
        "Resolve a drug NAME to its RxCUI and package NDCs. Call this FIRST whenever the user "
        "names a drug in prose instead of giving an identifier. Never guess an RxCUI or NDC -- "
        "if this returns no match or several, say so or ask which one."
    ),
    args=FindDrugArgs,
)
def find_drug_by_name(args: FindDrugArgs, principal: Principal) -> dict:
    term = (args.name or "").strip()
    if not term:
        return {"matches": [], "ambiguous": False}

    with Session(engine) as session:
        drugs = session.scalars(
            select(Drug).where(Drug.name.ilike(f"%{term}%"))
        ).all()

    if not drugs:
        return {"matches": [], "ambiguous": False}

    ndcs = [d.ndc for d in drugs]
    stock_map = _stock_totals(principal, ndcs)

    matches = []
    for d in drugs:
        on_hand = stock_map.get(d.ndc, 0)
        if args.stocked_only and on_hand <= 0:
            continue
        rxcui = (d.raw or {}).get("rxcui") or ""
        matches.append({
            "rxcui": str(rxcui),
            "ndc": str(d.ndc),
            "name": str(d.name or ""),
            "on_hand": on_hand,
        })

    matches.sort(key=lambda m: (-m["on_hand"], m["name"].lower()))
    return {"matches": matches, "ambiguous": len(matches) > 1}


class SearchAnaloguesArgs(BaseModel):
    rxcui: str = Field(description="RxCUI of the drug in shortage")
    mode: str = Field(
        "ingredient",
        description=(
            "'ingredient' for the same active ingredient (default), "
            "'full' for other ingredients in the same therapeutic class"
        ),
    )


@tool(
    permission="drug:search",
    description=(
        "Find therapeutic alternatives for a drug by RxCUI, ranked by this "
        "hospital's on-hand stock. Use when a pharmacist asks what to "
        "substitute for a drug that is short."
    ),
    args=SearchAnaloguesArgs,
)
def search_analogues_rxnorm(args: SearchAnaloguesArgs, principal: Principal) -> dict:
    try:
        candidates = (
            therapeutic_scd_sbd(args.rxcui) if args.mode == "full" else related_scd_sbd(args.rxcui)
        )
    except RxNormError:
        return {"error": "rxnorm unavailable"}

    candidates = [c for c in candidates if c["rxcui"] != args.rxcui]
    # ponytail: sequential, not analogue's route's ThreadPoolExecutor -- a
    # copilot turn is already one thread off the event loop (registry.py's
    # run_in_threadpool), and candidate counts here are small. Match the
    # route's concurrency if candidate lists grow large enough to matter.
    ndc_map = {c["rxcui"]: _ndcs_or_empty(c["rxcui"]) for c in candidates}
    totals = _stock_totals(principal, [n for ndcs in ndc_map.values() for n in ndcs])

    items = []
    for c in candidates:
        candidate_ndcs = ndc_map.get(c["rxcui"], [])
        qty = sum(totals.get(n, 0) for n in candidate_ndcs)
        items.append({
            "rxcui": c["rxcui"],
            "name": c["name"],
            "ndcs": candidate_ndcs[:3],
            "primary_ndc": candidate_ndcs[0] if candidate_ndcs else None,
            **stock_fields(qty),
        })
    items.sort(key=lambda row: (-row["quantity"], row["name"].lower(), row["rxcui"]))
    return {"items": items[:_KEEP_LIMIT]}


def _ndcs_or_empty(rxcui: str) -> list[str]:
    try:
        return ndcs_for_rxcui(rxcui)
    except RxNormError:
        return []


class CheckStockArgs(BaseModel):
    ndc: str | None = Field(None, description="NDC of the drug to check on-hand stock for")
    rxcui: str | None = Field(None, description="RxCUI of the drug to check on-hand stock for (if NDC is not known)")


@tool(
    permission="inventory:read",
    description=(
        "Look up this hospital's on-hand stock for a drug by NDC or RxCUI, broken down by "
        "storage location. Use when the user asks how much of a drug is in "
        "stock, in addition to whatever is already shown on screen -- this "
        "reads the database directly rather than the page's last snapshot."
    ),
    args=CheckStockArgs,
)
def check_stock_by_ndc(args: CheckStockArgs, principal: Principal) -> dict:
    if args.ndc:
        try:
            clean_ndc = ndc11(args.ndc)
        except ValueError as exc:
            return {"error": "incomplete_ndc", "message": str(exc), "input": args.ndc}
        target_ndcs = list(dict.fromkeys([args.ndc, clean_ndc]))
    elif args.rxcui:
        target_ndcs = _ndcs_or_empty(args.rxcui)
        if not target_ndcs:
            return {
                "rxcui": args.rxcui,
                "total_quantity": 0,
                "locations": [],
                "note": "No NDCs found for this RxCUI in RxNorm",
            }
    else:
        return {"error": "must provide either ndc or rxcui"}

    with session_scope(principal.hospital_id, principal.user_id) as session:
        rows = session.execute(
            select(StockSnapshot.location_id, StockSnapshot.quantity, StockSnapshot.updated_at)
            .where(StockSnapshot.ndc.in_(target_ndcs))
        ).all()
    res = {
        "ndc": args.ndc,
        "total_quantity": sum(int(qty or 0) for _, qty, _ in rows),
        "locations": [
            {
                "location_id": location_id or "hospital-wide",
                "quantity": int(qty or 0),
                "updated_at": updated_at.isoformat(),
            }
            for location_id, qty, updated_at in rows
        ],
    }
    if args.rxcui is not None:
        res["rxcui"] = args.rxcui
    return res


class SweepShelfArgs(BaseModel):
    status_filter: str = Field(
        "attention",
        description="'attention' for red/yellow only (default), 'all' for every stocked NDC",
    )
    facility_id: int | str | None = Field(
        None,
        description="Limit sweep to one facility by ID or code; omit for hospital-wide summary",
    )


# A hospital's whole formulary would blow a turn's context. This is a summary
# tool, not a bulk export -- see _KEEP_LIMIT above for the same idea applied
# to search_analogues_rxnorm.
_SWEEP_LIMIT = 50


@tool(
    permission="inventory:read",
    description=(
        "Review the compliance status of every NDC this hospital currently "
        "holds in stock, broken down by facility and hospital-wide, and report "
        "only the ones that need attention (red or yellow, plus anything with no "
        "certification record at all). Results include per-facility breakdowns and hospital totals."
    ),
    args=SweepShelfArgs,
)
def sweep_shelf_certificates(args: SweepShelfArgs, principal: Principal) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        fid = None
        if args.facility_id is not None:
            if isinstance(args.facility_id, int):
                fid = args.facility_id
            else:
                clean_fid = str(args.facility_id).removeprefix("fac-").strip()
                if clean_fid.isdigit():
                    fid = int(clean_fid)
                else:
                    fac_row = session.execute(
                        select(Facility.id).where(
                            (Facility.code == str(args.facility_id))
                            | (Facility.code == clean_fid)
                            | (Facility.name.ilike(f"%{args.facility_id}%"))
                        )
                    ).scalars().first()
                    if fac_row is not None:
                        fid = fac_row

        stmt = select(StockSnapshot.ndc, StockSnapshot.facility_id, StockSnapshot.quantity).where(
            StockSnapshot.quantity > 0
        )
        if fid is not None:
            stmt = stmt.where(StockSnapshot.facility_id == fid)
        stock_rows = session.execute(stmt).all()

    if not stock_rows:
        return {
            "checked": 0,
            "flagged": [],
            "unknown": [],
            "by_facility": {},
            "hospital_total": {"flagged_count": 0, "unknown_count": 0, "total_quantity": 0},
            "truncated": False,
        }

    ndcs = list(dict.fromkeys(str(r[0]) for r in stock_rows))
    totals: dict[str, int] = {}
    facility_stock: dict[str, dict[str, int]] = {}
    for ndc_val, fac_val, qty in stock_rows:
        ndc_str = str(ndc_val)
        fac_key = str(fac_val) if fac_val is not None else "unassigned"
        totals[ndc_str] = totals.get(ndc_str, 0) + int(qty or 0)
        facility_stock.setdefault(fac_key, {})[ndc_str] = (
            facility_stock.setdefault(fac_key, {}).get(ndc_str, 0) + int(qty or 0)
        )

    all_ndc_variants = list({n for ndc in ndcs for n in (ndc, ndc.replace("-", "").strip()) if n})

    # Reference tables, no hospital_id -- same split verify_batch_cert
    # documents.
    with Session(engine) as session:
        records_raw = session.execute(
            select(DrugCertification).where(DrugCertification.ndc.in_(all_ndc_variants))
        ).scalars()
        records: dict[str, DrugCertification] = {}
        for r in records_raw:
            records[str(r.ndc)] = r
            records[str(r.ndc).replace("-", "").strip()] = r

        drug_names: dict[str, str] = {}
        try:
            for d in session.execute(
                select(Drug.ndc, Drug.name).where(Drug.ndc.in_(all_ndc_variants))
            ):
                if hasattr(d, "__getitem__"):
                    drug_names[str(d[0])] = str(d[1] or "")
                    drug_names[str(d[0]).replace("-", "").strip()] = str(d[1] or "")
        except Exception:
            pass

        findings_by_ndc: dict[str, list[Finding]] = {}
        for ndc_val, code in session.execute(
            select(CertificationFinding.ndc, CertificationFinding.code).where(
                CertificationFinding.ndc.in_(all_ndc_variants)
            )
        ):
            k = str(ndc_val)
            findings_by_ndc.setdefault(k, []).append(
                Finding(code=code, message="", source="")
            )
            k_clean = k.replace("-", "").strip()
            if k_clean != k:
                findings_by_ndc.setdefault(k_clean, []).append(
                    Finding(code=code, message="", source="")
                )

    flagged, unknown = [], []
    for ndc in ndcs:
        record = records.get(ndc) or records.get(ndc.replace("-", "").strip())
        name = drug_names.get(ndc) or drug_names.get(ndc.replace("-", "").strip())
        if record is None:
            unknown.append(ndc)
            continue
        findings_list = findings_by_ndc.get(ndc) or findings_by_ndc.get(ndc.replace("-", "").strip()) or []
        detail = signal(findings_list)
        status = record.status  # stored colour wins, same as verify_batch_cert
        if args.status_filter != "all" and status not in ("red", "yellow"):
            continue
        reasons = [RULES[c].explain for c in detail["codes"] if c in RULES] or detail["codes"]
        flagged.append(
            {
                "ndc": ndc,
                "name": name,
                "status": status,
                "quantity": totals.get(ndc, 0),
                "reasons": reasons,
                "codes": detail["codes"],
            }
        )
    flagged.sort(key=lambda row: (row["status"] != "red", -row["quantity"]))

    by_facility: dict[str, list[dict]] = {}
    for fac_key, f_stock in facility_stock.items():
        fac_flagged = []
        for ndc, qty in f_stock.items():
            record = records.get(ndc) or records.get(ndc.replace("-", "").strip())
            name = drug_names.get(ndc) or drug_names.get(ndc.replace("-", "").strip())
            if record is None:
                continue
            findings_list = findings_by_ndc.get(ndc) or findings_by_ndc.get(ndc.replace("-", "").strip()) or []
            detail = signal(findings_list)
            status = record.status
            if args.status_filter != "all" and status not in ("red", "yellow"):
                continue
            reasons = [RULES[c].explain for c in detail["codes"] if c in RULES] or detail["codes"]
            fac_flagged.append({
                "ndc": ndc,
                "name": name,
                "status": status,
                "quantity": qty,
                "reasons": reasons,
                "codes": detail["codes"],
            })
        if fac_flagged:
            fac_flagged.sort(key=lambda row: (row["status"] != "red", -row["quantity"]))
            by_facility[fac_key] = fac_flagged

    return {
        "checked": len(ndcs),
        "flagged": flagged[:_SWEEP_LIMIT],
        "unknown": unknown[:_SWEEP_LIMIT],
        "by_facility": by_facility,
        "hospital_total": {
            "flagged_count": len(flagged),
            "unknown_count": len(unknown),
            "total_quantity": sum(f["quantity"] for f in flagged),
        },
        "truncated": len(flagged) > _SWEEP_LIMIT or len(unknown) > _SWEEP_LIMIT,
    }


class VerifyBatchCertArgs(BaseModel):
    ndc: str = Field(description="NDC of the drug/batch to check compliance status for")


@tool(
    permission="certificate:read",
    description=(
        "Look up the compliance traffic-light status (green/yellow/red) and "
        "findings for one NDC -- expired listing, open recall, etc."
    ),
    args=VerifyBatchCertArgs,
)
def verify_batch_cert(args: VerifyBatchCertArgs, principal: Principal) -> dict:
    try:
        clean_ndc = ndc11(args.ndc)
    except ValueError as exc:
        return {"error": "incomplete_ndc", "message": str(exc), "input": args.ndc}

    # drug_certification has no hospital_id/RLS -- reference data, same as
    # compliance's own main.py, which is why this is a plain Session and not
    # session_scope (there is no tenant context to set).
    with Session(engine) as session:
        record = session.execute(
            select(DrugCertification).where(
                (DrugCertification.ndc == args.ndc)
                | (DrugCertification.ndc == clean_ndc)
            )
        ).scalars().first()
        try:
            drug = session.execute(
                select(Drug).where(
                    (Drug.ndc == args.ndc)
                    | (Drug.ndc == clean_ndc)
                )
            ).scalars().first()
        except Exception:
            drug = None
        if record is None:
            return {
                "ndc": args.ndc,
                "name": getattr(drug, "name", None) if drug else None,
                "status": "unknown",
                "findings": [],
                "note": "No FDA certification record is held for this NDC.",
            }

        findings = (
            session.execute(
                select(CertificationFinding).where(
                    (CertificationFinding.ndc == record.ndc)
                    | (CertificationFinding.ndc == args.ndc)
                    | (CertificationFinding.ndc == clean_ndc)
                )
            )
            .scalars()
            .all()
        )

    detail = signal(
        [Finding(code=f.code, message=f.message, source=f.source) for f in findings]
    )
    detail["name"] = getattr(drug, "name", None) if drug else None
    detail["reasons"] = [RULES[c].explain for c in detail["codes"] if c in RULES] or detail["codes"]
    # Stored colour wins over the recomputed one -- it is what the last
    # ingest run decided, same rule compliance's GET /status follows.
    detail["status"] = record.status
    detail["ruleset_version"] = record.ruleset_version
    return {"ndc": args.ndc, **detail}


class StorageExcursionArgs(BaseModel):
    facility_id: int | str | None = Field(None, description="Limit to one facility by ID or code; omit for all")
    window_hours: int = Field(24, description="Lookback window in hours for excursion telemetry")


# The model narrates a breach; it does not get to decide the stock is
# unusable off the back of it -- ranked worst first so trimming to this many
# keeps the ones that actually matter.
_EXCURSION_LIMIT = 30


@tool(
    permission="facility:read",
    description=(
        "Report storage-condition violations -- a drug held outside its "
        "required temperature or humidity range, based on sensor telemetry. "
        "Use when the user asks about cold-chain problems, fridge/freezer "
        "excursions, or storage compliance. This reports the breach; it "
        "does not conclude the stock is unusable -- that is a human call."
    ),
    args=StorageExcursionArgs,
)
def list_storage_excursions(args: StorageExcursionArgs, principal: Principal) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        # Already worst-first -- excursions() orders by breach duration.
        rows = excursions(session, args.facility_id)
        fid = None
        if args.facility_id is not None:
            if isinstance(args.facility_id, int):
                fid = args.facility_id
            else:
                clean_fid = str(args.facility_id).removeprefix("fac-").strip()
                if clean_fid.isdigit():
                    fid = int(clean_fid)
                else:
                    fac_row = session.execute(
                        select(Facility.id).where(
                            (Facility.code == str(args.facility_id))
                            | (Facility.code == clean_fid)
                            | (Facility.name.ilike(f"%{args.facility_id}%"))
                        )
                    ).scalars().first()
                    if fac_row is not None:
                        fid = fac_row

        loc_stmt = select(func.count(StorageLocation.id))
        rep_stmt = select(func.count(func.distinct(LocationCondition.location_id)))
        read_stmt = select(func.count(LocationCondition.id))
        if fid is not None:
            loc_stmt = loc_stmt.where(StorageLocation.facility_id == fid)
            rep_stmt = rep_stmt.join(StorageLocation, StorageLocation.id == LocationCondition.location_id).where(StorageLocation.facility_id == fid)
            read_stmt = read_stmt.join(StorageLocation, StorageLocation.id == LocationCondition.location_id).where(StorageLocation.facility_id == fid)

        location_count = session.scalar(loc_stmt) or 0
        reporting_count = session.scalar(rep_stmt) or 0
        reading_count = session.scalar(read_stmt) or 0

    return {
        "checked": len(rows),
        "excursions": rows[:_EXCURSION_LIMIT],
        "locations_monitored": location_count,
        "locations_reporting": reporting_count,
        "readings_checked": reading_count,
        "window_hours": args.window_hours,
        "truncated": len(rows) > _EXCURSION_LIMIT,
    }


class ExploreNdcArgs(BaseModel):
    ndc: str = Field(description="NDC to research against openFDA and RxNorm for a drug nobody has looked up before")


@tool(
    permission="certification:explore",
    description=(
        "Research an NDC that has no certification record yet -- a drug "
        "nobody has stocked or checked before -- against openFDA's NDC "
        "directory and RxNorm's status feed, live. Spends a shared, rate-"
        "limited openFDA budget, so only use it when verify_batch_cert or "
        "sweep_shelf_certificates has already come back 'unknown' for this "
        "NDC and the user wants it resolved, not for an NDC already "
        "certified."
    ),
    args=ExploreNdcArgs,
)
def explore_ndc(args: ExploreNdcArgs, principal: Principal) -> dict:
    try:
        ndc11(args.ndc)
    except ValueError as exc:
        return {"error": "incomplete_ndc", "message": str(exc), "input": args.ndc}
    # Reference table, no hospital_id -- same split verify_batch_cert
    # documents. explore() does its own commit.
    with Session(engine) as session:
        return explore(session, args.ndc)


class PatientRegimenArgs(BaseModel):
    patient_id: str = Field(
        description=(
            "The patient's UUID if you already have it, otherwise their full name "
            "exactly as the user typed it (e.g. 'John Smith'). A name that matches "
            "more than one patient ends this turn with a disambiguation prompt for "
            "the user -- do not guess which one they meant."
        )
    )


@tool(
    permission="patient:read",
    description=(
        "Look up what a patient's profile carries -- allergies, conditions, "
        "and pharmacogenomic phenotypes -- summarised so a physician can ask "
        "before prescribing instead of opening the chart. This system does "
        "not track an active medication list, so no current-therapy RxCUIs "
        "are returned; ask the patient or check the chart for that."
    ),
    args=PatientRegimenArgs,
)
def get_patient_regimen(args: PatientRegimenArgs, principal: Principal) -> dict:
    # PatientAmbiguous propagates uncaught -- the copilot route turns it into
    # a disambiguation event before any name/DOB can reach Gemini.
    patient_uuid = resolve_patient_ref(principal, args.patient_id)
    if patient_uuid is None:
        return {"error": "patient not found"}

    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = session.get(Patient, patient_uuid)
        if row is None or row.hospital_id != principal.hospital_uuid:
            return {"error": "patient not found"}
        # PHI boundary: no full_name, no date_of_birth -- an age band only,
        # same de-identification the assessment path already performs. This
        # tool result is sent to Gemini, so the identifiers stop here.
        return {
            "age_band": age_band_from_dob(row.date_of_birth),
            "blood_group": row.blood_group,
            "allergy_codes": list(row.allergy_codes or []),
            "condition_codes": list(row.condition_codes or []),
            "pgx_phenotypes": list(row.pgx_phenotypes or []),
        }


class AssessPatientArgs(BaseModel):
    patient_id: str = Field(
        description=(
            "The patient's UUID if you already have it, otherwise their full name "
            "exactly as the user typed it (e.g. 'John Smith'). A name that matches "
            "more than one patient ends this turn with a disambiguation prompt for "
            "the user -- do not guess which one they meant."
        )
    )
    rxcui: str = Field(description="RxCUI of the drug being considered")


@tool(
    permission="profile:assess",
    description=(
        "Run the deterministic safety rules for one patient against one "
        "candidate drug -- allergy and duplicate-ingredient hard gates, "
        "interactions with their regimen, renal/hepatic limits, age caution, "
        "prior ADR history, and any approved label or pharmacogenomic "
        "findings. Use before answering whether a drug is safe to start on "
        "this patient. The verdict is the rules engine's arithmetic -- "
        "report it verbatim, never soften, override, or recompute it. To check "
        "physical availability/stock of the candidate drug, call "
        "check_stock_by_ndc(rxcui=...). If the verdict is 'blocked' or the drug "
        "is out of stock, search_analogues_rxnorm is the natural next call."
    ),
    args=AssessPatientArgs,
)
def assess_patient_for_drug(args: AssessPatientArgs, principal: Principal) -> dict:
    return _assess_for_drug(principal, args.patient_id, args.rxcui)


class ExplainAssessmentArgs(BaseModel):
    request_id: str = Field(description="The request_id returned by a prior assessment")


@tool(
    permission="profile:explain",
    description=(
        "Explain a previously logged safety assessment by its request_id -- "
        "every finding that contributed to the score, its weight, and its "
        "share of the total. Every stage is deterministic arithmetic, so "
        "only narrate numbers that appear in this tool's result; never state "
        "a number that is not in the returned contributions."
    ),
    args=ExplainAssessmentArgs,
)
def explain_assessment(args: ExplainAssessmentArgs, principal: Principal) -> dict:
    result = _explain_assessment(principal, args.request_id)
    if result == NOT_FOUND:
        return {"error": "no such assessment"}
    if result == UNAVAILABLE:
        return {"error": "assessment log unavailable"}
    return result


class GetStockArgs(BaseModel):
    ndc: str = Field(description="Package NDC to look up on-hand quantity for")
    facility_id: int | None = Field(None, description="Optional facility integer id")


@tool(
    permission="inventory:read",
    description="Return on-hand quantity for one NDC, optionally at one facility.",
    args=GetStockArgs,
)
def get_stock(args: GetStockArgs, principal: Principal) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        stmt = select(StockSnapshot.ndc, func.coalesce(func.sum(StockSnapshot.quantity), 0))
        stmt = stmt.where(StockSnapshot.ndc == args.ndc)
        if args.facility_id is not None:
            stmt = stmt.where(StockSnapshot.facility_id == args.facility_id)
        row = session.execute(stmt.group_by(StockSnapshot.ndc)).first()
        qty = int(row[1]) if row else 0
    return {"ndc": args.ndc, "facility_id": args.facility_id, "quantity": qty}


find_analogues = search_analogues_rxnorm


class CheckCertificateArgs(BaseModel):
    ndc: str = Field(description="NDC to look up the compliance traffic light for")


@tool(
    permission="certificate:read",
    description="Look up certification status for one NDC (same as GET /compliance/status).",
    args=CheckCertificateArgs,
)
def check_certificate(args: CheckCertificateArgs, principal: Principal) -> dict:
    return verify_batch_cert(VerifyBatchCertArgs(ndc=args.ndc), principal)


class GetForecastArgs(BaseModel):
    ndc: str = Field(description="NDC whose latest forecast run should be summarised")
    facility_id: int | None = Field(None, description="Optional facility integer id")


@tool(
    permission="forecast:read",
    description="Summarise the latest demand forecast for one NDC (p50 next 7 days and run id).",
    args=GetForecastArgs,
)
def get_forecast(args: GetForecastArgs, principal: Principal) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        stmt = select(ForecastPoint).where(ForecastPoint.ndc == args.ndc)
        if args.facility_id is not None:
            stmt = stmt.where(ForecastPoint.facility_id == args.facility_id)
        latest = session.execute(
            stmt.order_by(ForecastPoint.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        if latest is None:
            return {"ndc": args.ndc, "run_id": None, "points": []}
        points = session.scalars(
            select(ForecastPoint)
            .where(ForecastPoint.run_id == latest.run_id, ForecastPoint.ndc == args.ndc)
            .order_by(ForecastPoint.target_date)
            .limit(7)
        ).all()
        return {
            "ndc": args.ndc,
            "run_id": latest.run_id,
            "model_version": latest.model_version,
            "points": [
                {"date": p.target_date.isoformat(), "p50": float(p.p50)} for p in points
            ],
        }


class AtRiskArgs(BaseModel):
    facility_id: int | str | None = Field(None, description="Limit to one facility by ID or code; omit for all")
    within_days: int = Field(30, ge=1, le=HORIZON_DAYS, description="Only SKUs depleting within this many days")
    surge_pct: int = Field(100, ge=100, le=300, description="100 = baseline demand; >100 = a surge scenario")


_AT_RISK_LIMIT = 30


@tool(
    permission="forecast:read",
    description=(
        "List stocked NDCs whose forecast depletes within a given number of "
        "days -- worst first. Use for questions like 'what is about to run "
        "out' or 'what needs restocking soon', across the whole formulary "
        "rather than one drug at a time."
    ),
    args=AtRiskArgs,
)
def list_at_risk_skus(args: AtRiskArgs, principal: Principal) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        result = _at_risk_skus(session, args.facility_id, args.within_days, args.surge_pct)
    items = result["items"]
    res = {
        "run_id": result["run_id"],
        "data_through": result["data_through"],
        "skus_evaluated": result.get("skus_evaluated", len(items)),
        "checked": len(items),
        "items": items[:_AT_RISK_LIMIT],
        "truncated": len(items) > _AT_RISK_LIMIT,
    }
    if not result["run_id"]:
        res["note"] = (
            "No forecast run has been computed for this hospital yet. "
            "Depletion estimates cannot be projected without a trained forecast run or consumption history. "
            "Advise the user that they can trigger a forecast run from the Forecasts page."
        )
    return res


class CheckForecastStalenessArgs(BaseModel):
    facility_id: int | str | None = Field(None, description="Unused today -- forecast runs are hospital-wide")


ProposeRerunArgs = CheckForecastStalenessArgs


@tool(
    permission="forecast:read",
    description=(
        "Check whether this hospital's forecast is stale -- report the last "
        "run's timestamp and the most recent consumption data available. "
        "Use when the user asks about forecast freshness or whether a re-run "
        "is needed. This tool is read-only and never triggers a run itself; "
        "triggering a real run is a human action done via the 'Re-run Forecast' "
        "button on the Forecasts page."
    ),
    args=CheckForecastStalenessArgs,
)
def check_forecast_staleness(args: CheckForecastStalenessArgs, principal: Principal) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        run = _latest_run(session)
    if run is None:
        return {"has_run": False, "note": "No forecast has ever been run for this hospital."}
    run_id, data_through, created_at = run
    return {
        "has_run": True,
        "run_id": run_id,
        "data_through": data_through.isoformat(),
        "generated_at": created_at.isoformat(),
        "note": "This is a report, not an action -- direct the user to the Re-run Forecast button on the Forecasts page to actually trigger a new run.",
    }


propose_forecast_rerun = check_forecast_staleness


class AuditQueryArgs(BaseModel):
    days: int = Field(30, description="Look-back window in days")
    task_type: str | None = Field(None, description="e.g. 'copilot'")
    outcome: str | None = Field(None, description="live | cache_hit | error | breaker_open")
    request_id: str | None = Field(None, description="Exact request_id to look up provenance for")


@tool(
    permission="audit:read",
    description=(
        "Aggregate outcomes of AI copilot turns for this hospital: counts by "
        "outcome, tool frequency, latency percentiles. This is INFRASTRUCTURE "
        "telemetry about the assistant itself -- it is not a clinical quality "
        "metric and must never be presented as one. Only the most recent 10 "
        "turns are individually inspectable."
    ),
    args=AuditQueryArgs,
)
def query_ai_decisions(args: AuditQueryArgs, principal: Principal) -> dict:
    return _query_ai_decisions(
        principal.hospital_id,
        days=args.days,
        task_type=args.task_type,
        outcome=args.outcome,
        request_id=args.request_id,
    )


class ReviewQueueArgs(BaseModel):
    status: str = Field(
        "awaiting_approval",
        description="'awaiting_approval' (default), 'approved', 'rejected', or 'all'",
    )


# Worst-first, same idea as PROGNOSIS_BASE in shared/medstock_shared/patient.py
# -- a queue is as urgent as its most serious pending item.
_SERIOUSNESS_RANK = {"fatal": 0, "serious": 1, "moderate": 2}
_QUEUE_PEEK = 10


@tool(
    permission="profile:review",
    description=(
        "Summarise the label-derived risk-profile review queue: how many "
        "are awaiting approval, already ruled on, and the accept rate, plus "
        "the most serious few items pending review. Use for questions like "
        "'what is waiting on a pharmacist' or 'is anything urgent in the "
        "queue' rather than opening the queue card by card."
    ),
    args=ReviewQueueArgs,
)
def list_review_queue(args: ReviewQueueArgs, principal: Principal) -> dict:
    if args.status != "all" and args.status not in PROFILE_STATUSES:
        return {"error": f"status must be 'all' or one of {PROFILE_STATUSES}"}
    items, counts = _load_queue(args.status, None, limit=200)
    items.sort(key=lambda r: _SERIOUSNESS_RANK.get(str(r["seriousness"]).lower(), 9))
    queue_total = sum(counts.values()) if isinstance(counts, dict) else len(items)
    return {
        "queue_total": queue_total,
        "counts": counts,
        "accept_rate": accept_rate(counts),
        "most_urgent": [
            {"rxcui": r["rxcui"], "reaction": r["reaction"], "seriousness": r["seriousness"], "citation": r["citation"]}
            for r in items[:_QUEUE_PEEK]
        ],
    }


class DraftOrderArgs(BaseModel):
    facility_id: int = Field(description="Operated facility that will receive the stock")
    supplier_id: int = Field(description="Supplier catalog id")
    ndc: str = Field(description="NDC to order")
    quantity: int = Field(gt=0, description="Requested quantity; rounded to pack size")
    review_decision_id: int = Field(
        description=(
            "Pending restock recommendation id this draft approves. You MUST obtain this "
            "from list_review_queue or from the user in this conversation. Never guess it -- "
            "a wrong id links the order to an approval for a different drug."
        )
    )


@tool(
    permission="order:write",
    description=(
        "Create a draft purchase order (never placed). Requires a review_decision_id "
        "from POST /inventory/recommendations. A physician token will 403."
    ),
    args=DraftOrderArgs,
)
def draft_order(args: DraftOrderArgs, principal: Principal) -> dict:
    import uuid as uuid_mod

    from ...models import ReviewDecision

    try:
        actor = uuid_mod.UUID(principal.user_id)
        hospital = principal.hospital_uuid
    except ValueError:
        return {"error": "invalid principal"}
    with session_scope(principal.hospital_id, principal.user_id) as session:
        decision = session.get(ReviewDecision, args.review_decision_id)
        if decision is None:
            return {"error": "review_decision not found"}
        if decision.hospital_id != principal.hospital_uuid:
            return {"error": "review_decision not found"}
        approved_ndc = (decision.payload or {}).get("ndc")
        if approved_ndc != args.ndc:
            return {
                "error": "review_decision_mismatch",
                "message": (
                    f"Review decision {args.review_decision_id} approves NDC {approved_ndc}, "
                    f"not {args.ndc}. Fetch the correct decision id before drafting."
                ),
            }

        sig = signal_for_ndc(session, args.ndc)
        if sig.status == "red":
            return {
                "error": "compliance_blocked",
                "ndc": args.ndc,
                "status": "red",
                "codes": sig.codes,
                "message": (
                    "This NDC is under an open compliance block (see codes). "
                    "A draft order was not created. A human must clear the block first."
                ),
            }
        warning = None
        if sig.status in ("yellow", "unknown"):
            warning = {"status": sig.status, "codes": sig.codes}

        order = create_purchase_order(
            session,
            hospital_id=hospital,
            actor_id=actor,
            facility_id=args.facility_id,
            supplier_id=args.supplier_id,
            status="draft",
            source="ai_suggestion",
            lines=[{"ndc": args.ndc, "quantity": args.quantity}],
            review_decision_id=args.review_decision_id,
        )
        return {
            "id": order.id,
            "ref": order.ref,
            "status": order.status,
            "compliance": warning,
        }

