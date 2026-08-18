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
from ...models import CertificationFinding, DrugCertification, StockSnapshot
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


class CheckStockArgs(BaseModel):
    ndc: str = Field(description="NDC of the drug to check on-hand stock for")


@tool(
    permission="inventory:read",
    description=(
        "Look up this hospital's on-hand stock for one NDC, broken down by "
        "storage location. Use when the user asks how much of a drug is in "
        "stock, in addition to whatever is already shown on screen -- this "
        "reads the database directly rather than the page's last snapshot."
    ),
    args=CheckStockArgs,
)
def check_stock_by_ndc(args: CheckStockArgs, principal: Principal) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        rows = session.execute(
            select(StockSnapshot.location_id, StockSnapshot.quantity, StockSnapshot.updated_at)
            .where(StockSnapshot.ndc == args.ndc)
        ).all()
    return {
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


class SweepShelfArgs(BaseModel):
    status_filter: str = Field(
        "attention",
        description="'attention' for red/yellow only (default), 'all' for every stocked NDC",
    )


# A hospital's whole formulary would blow a turn's context. This is a summary
# tool, not a bulk export -- see _KEEP_LIMIT above for the same idea applied
# to search_analogues_rxnorm.
_SWEEP_LIMIT = 50


@tool(
    permission="inventory:read",
    description=(
        "Review the compliance status of every NDC this hospital currently "
        "holds in stock, and report only the ones that need attention (red "
        "or yellow, plus anything with no certification record at all). Use "
        "when the user asks what on the shelf needs attention or has gone "
        "red, rather than about one drug."
    ),
    args=SweepShelfArgs,
)
def sweep_shelf_certificates(args: SweepShelfArgs, principal: Principal) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        ndcs = [
            str(ndc)
            for ndc in session.scalars(
                select(StockSnapshot.ndc)
                .where(StockSnapshot.quantity > 0)
                .distinct()
            ).all()
        ]
    if not ndcs:
        return {"checked": 0, "flagged": [], "unknown": [], "truncated": False}
    totals = _stock_totals(principal, ndcs)

    # Reference tables, no hospital_id -- same split verify_batch_cert
    # documents.
    with Session(engine) as session:
        records = {
            str(r.ndc): r
            for r in session.execute(
                select(DrugCertification).where(DrugCertification.ndc.in_(ndcs))
            ).scalars()
        }
        findings_by_ndc: dict[str, list[Finding]] = {}
        for ndc, code in session.execute(
            select(CertificationFinding.ndc, CertificationFinding.code).where(
                CertificationFinding.ndc.in_(ndcs)
            )
        ):
            findings_by_ndc.setdefault(str(ndc), []).append(
                Finding(code=code, message="", source="")
            )

    flagged, unknown = [], []
    for ndc in ndcs:
        record = records.get(ndc)
        if record is None:
            unknown.append(ndc)
            continue
        detail = signal(findings_by_ndc.get(ndc, []))
        status = record.status  # stored colour wins, same as verify_batch_cert
        if args.status_filter != "all" and status not in ("red", "yellow"):
            continue
        flagged.append(
            {"ndc": ndc, "status": status, "quantity": totals.get(ndc, 0), "codes": detail["codes"]}
        )
    flagged.sort(key=lambda row: (row["status"] != "red", -row["quantity"]))

    return {
        "checked": len(ndcs),
        "flagged": flagged[:_SWEEP_LIMIT],
        "unknown": unknown[:_SWEEP_LIMIT],
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
