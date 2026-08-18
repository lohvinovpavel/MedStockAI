"""Pharmacist-scoped copilot tools.

Only two: `search_analogues_rxnorm` and `verify_batch_cert`, both thin
wrappers around reads `medstock_shared` already implements for the analogue
and compliance services. `check_bioequivalence`, named in the original brief
alongside these two, is deliberately not here -- there is no bioequivalence
data or logic anywhere in this repo to wrap. Declaring it to Gemini with
nothing behind it would make it a hallucination generator, exactly the
failure mode docs/ai-module-plan.md Phase 4 rules out for
`generate_draft_po`/`approve_and_send_po`/`approve_emergency_protocol`.
Add it here, for real, once that logic exists somewhere.
"""

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...auth import Principal
from ...certification import Finding, signal
from ...db import engine, session_scope
from ...models import (
    CertificationFinding,
    DrugCertification,
    ForecastPoint,
    StockSnapshot,
)
from ...ordering import create_purchase_order
from ...rxnorm import RxNormError, ndcs_for_rxcui, related_scd_sbd, therapeutic_scd_sbd
from ...stock import stock_fields
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
        qty = sum(totals.get(n, 0) for n in ndc_map.get(c["rxcui"], []))
        items.append({"rxcui": c["rxcui"], "name": c["name"], **stock_fields(qty)})
    items.sort(key=lambda row: (-row["quantity"], row["name"].lower(), row["rxcui"]))
    return {"items": items[:_KEEP_LIMIT]}


def _ndcs_or_empty(rxcui: str) -> list[str]:
    try:
        return ndcs_for_rxcui(rxcui)
    except RxNormError:
        return []


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
    # drug_certification has no hospital_id/RLS -- reference data, same as
    # compliance's own main.py, which is why this is a plain Session and not
    # session_scope (there is no tenant context to set).
    with Session(engine) as session:
        record = session.execute(
            select(DrugCertification).where(DrugCertification.ndc == args.ndc)
        ).scalar_one_or_none()
        if record is None:
            return {"ndc": args.ndc, "status": "unknown", "findings": []}

        findings = (
            session.execute(
                select(CertificationFinding).where(CertificationFinding.ndc == args.ndc)
            )
            .scalars()
            .all()
        )

    detail = signal(
        [Finding(code=f.code, message=f.message, source=f.source) for f in findings]
    )
    # Stored colour wins over the recomputed one -- it is what the last
    # ingest run decided, same rule compliance's GET /status follows.
    detail["status"] = record.status
    detail["ruleset_version"] = record.ruleset_version
    return {"ndc": args.ndc, **detail}


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


class FindAnaloguesArgs(BaseModel):
    rxcui: str = Field(description="RxCUI of the drug to find substitutes for")


@tool(
    permission="drug:search",
    description="Find analogue substitutes for an RxCUI using the same graph as analogue search.",
    args=FindAnaloguesArgs,
)
def find_analogues(args: FindAnaloguesArgs, principal: Principal) -> dict:
    return search_analogues_rxnorm(
        SearchAnaloguesArgs(rxcui=args.rxcui, mode="ingredient"), principal
    )


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


class DraftOrderArgs(BaseModel):
    facility_id: int = Field(description="Operated facility that will receive the stock")
    supplier_id: int = Field(description="Supplier catalog id")
    ndc: str = Field(description="NDC to order")
    quantity: int = Field(gt=0, description="Requested quantity; rounded to pack size")
    review_decision_id: int = Field(
        description="Pending restock recommendation id this draft is approving"
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
        hospital = uuid_mod.UUID(principal.hospital_id)
    except ValueError:
        return {"error": "invalid principal"}
    with session_scope(principal.hospital_id, principal.user_id) as session:
        decision = session.get(ReviewDecision, args.review_decision_id)
        if decision is None:
            return {"error": "review_decision not found"}
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
        return {"id": order.id, "ref": order.ref, "status": order.status}

