import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from medstock_shared.auth import Principal, require
from medstock_shared.config import settings
from medstock_shared.db import engine, session_scope
from medstock_shared.models import FormularyItem, StockSnapshot
from medstock_shared.rxnorm import (
    ANALOGUE_CANDIDATE_LIMIT,
    RxNormError,
    apply_formulary,
    concept_properties,
    ingredients_for_rxcui,
    ndcs_for_rxcui,
    related_scd_sbd,
    search_concepts,
    therapeutic_scd_sbd,
)
from medstock_shared.stock import stock_fields
from sqlalchemy import func, select, text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

app = FastAPI(title="analogue")
drugs = APIRouter()
_log = logging.getLogger("analogue")

_NDC_WORKERS = 4
AI_ANALOGUE_KEEP_LIMIT = 5
AnalogueMode = Literal["ingredient", "full", "orange_book", "class"]
_UNIMPLEMENTED_MODE = {
    "orange_book": "Orange Book analogue search is not implemented yet",
    "class": "Pharmacologic-class analogue search is not implemented yet",
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
    the installed medstock-analogue package (pyproject.toml), not the image."""
    try:
        semver = pkg_version("medstock-analogue")
    except PackageNotFoundError:
        semver = "unknown"
    return {"service": "analogue", "version": os.environ.get("GIT_SHA", "unknown"), "semver": semver}


def load_formulary_rxcuis(session) -> set[str]:
    try:
        return set(session.scalars(select(FormularyItem.rxcui)).all())
    except ProgrammingError:
        session.rollback()
        return set()


def formulary_for(principal: Principal) -> set[str]:
    try:
        with session_scope(principal.hospital_id, principal.user_id) as session:
            return load_formulary_rxcuis(session)
    except SQLAlchemyError:
        return set()


def _ndcs_or_empty(rxcui: str) -> list[str]:
    try:
        return ndcs_for_rxcui(rxcui)
    except RxNormError:
        return []


def stock_totals_by_ndc(principal: Principal, ndcs: list[str]) -> dict[str, int]:
    """Hospital on-hand per NDC from stock_snapshot. Direct DB read — no inventory HTTP."""
    unique = list(dict.fromkeys(ndcs))
    if not unique:
        return {}
    try:
        with session_scope(principal.hospital_id, principal.user_id) as session:
            rows = session.execute(
                select(
                    StockSnapshot.ndc,
                    func.coalesce(func.sum(StockSnapshot.quantity), 0),
                )
                .where(StockSnapshot.ndc.in_(unique))
                .group_by(StockSnapshot.ndc)
            ).all()
            return {str(ndc): int(qty or 0) for ndc, qty in rows}
    except SQLAlchemyError:
        return {}


@drugs.get("/drugs/search")
def drugs_search(
    q: str = Query(..., min_length=1, max_length=120),
    limit: int = Query(20, ge=1, le=50),
    principal: Principal = Depends(require("drug:search")),
) -> dict:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=422, detail="q must not be blank")
    try:
        hits = search_concepts(query, limit)
    except RxNormError as exc:
        raise HTTPException(status_code=503, detail="rxnorm unavailable") from exc
    items = apply_formulary(hits, formulary_for(principal), limit)
    return {"query": query, "items": items}


@drugs.get("/drugs/{rxcui}/packages")
def drug_packages(
    rxcui: str,
    principal: Principal = Depends(require("drug:search")),
) -> dict:
    try:
        ndcs = ndcs_for_rxcui(rxcui.strip())
    except RxNormError as exc:
        raise HTTPException(status_code=503, detail="rxnorm unavailable") from exc
    totals = stock_totals_by_ndc(principal, ndcs)
    quantity = sum(totals.get(ndc, 0) for ndc in ndcs)
    return {
        "rxcui": rxcui.strip(),
        "packages": [{"ndc": ndc} for ndc in ndcs],
        **stock_fields(quantity),
    }


def _plain(value: object) -> str:
    """Strip braces so payload values are safe for str.format() prompts."""
    return str(value).replace("{", "(").replace("}", ")")


def _source_drug_name(rxcui: str) -> str:
    try:
        props = concept_properties(rxcui)
    except RxNormError:
        return rxcui
    name = (props or {}).get("name") or ""
    return name or rxcui


def _analogue_source_text(drug_name: str, rxcui: str, items: list[dict]) -> str:
    parts = [
        f"The source drug is {_plain(drug_name)}, RxCUI {_plain(rxcui)}, which is in shortage.",
        (
            "Candidates are other ingredients in the same pharmacologic class, "
            "not the same ingredient."
        ),
    ]
    for row in items:
        parts.append(
            f"Candidate {_plain(row['rxcui'])} is {_plain(row['name'])}, "
            f"type {_plain(row['tty'])}, "
            f"stock band {_plain(row['stock_status'])}, quantity {int(row['quantity'])} packs."
        )
    return " ".join(parts)


def _analogue_candidate_lines(items: list[dict]) -> str:
    return "\n".join(
        f"{_plain(row['rxcui'])} | {_plain(row['tty'])} | {_plain(row['name'])} | "
        f"quantity={int(row['quantity'])} | stock={_plain(row['stock_status'])}"
        for row in items
    )


def _ai_available() -> bool:
    """True only when GEMINI_API_KEY / settings.gemini_api_key is non-blank."""
    return bool((settings.gemini_api_key or "").strip())


def _ask_analogue_ai(payload: dict) -> dict:
    """Lazy import so UC-1/UC-3 tests never construct a Gemini client."""
    from medstock_shared import ask_ai

    return ask_ai("analogue", payload)


def _filter_full_with_ai(source: str, items: list[dict]) -> tuple[list[dict], bool]:
    """UC-5: closed-world keep-set from Gemini. Empty or failed → unfiltered + flag."""
    try:
        drug_name = _source_drug_name(source)
        source_text = _analogue_source_text(drug_name, source, items)
        payload = {
            "drug_name": _plain(drug_name),
            "rxcui": _plain(source),
            "candidates": _analogue_candidate_lines(items),
            "source_text": source_text,
        }
        result = _ask_analogue_ai(payload)
        raw_items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(raw_items, list) or not raw_items:
            _log.info("Gemini keep-set empty for rxcui %s; falling back to unfiltered Full", source)
            return items, True

        by_rxcui = {row["rxcui"]: row for row in items}
        kept: list[dict] = []
        seen: set[str] = set()
        for llm_item in raw_items:
            if not isinstance(llm_item, dict):
                continue
            rid = str(llm_item.get("rxcui") or "").strip()
            if not rid or rid not in by_rxcui or rid in seen:
                continue
            seen.add(rid)
            row = dict(by_rxcui[rid])
            reason = llm_item.get("rationale") or llm_item.get("reason") or ""
            citation = llm_item.get("citation") or ""
            if reason:
                row["reason"] = str(reason)
            if citation:
                row["citation"] = str(citation)
            kept.append(row)

        if not kept:
            _log.info("Gemini keep-set empty for rxcui %s; falling back to unfiltered Full", source)
            return items, True
        kept.sort(key=lambda row: (-row["quantity"], row["name"].lower(), row["rxcui"]))
        kept = kept[:AI_ANALOGUE_KEEP_LIMIT]
        _log.info("Gemini keep-set rxcui=%s kept=%s", source, len(kept))
        return kept, False
    except Exception:  # noqa: BLE001 — best-effort AI filter, any failure falls back to unfiltered
        _log.exception("Gemini analogue filter failed for rxcui %s", source)
        return items, True


@drugs.get("/analogues/ai-status")
def analogue_ai_status(
    principal: Principal = Depends(require("drug:search")),
) -> dict:
    """Whether UC-5 Gemini filtering can be turned on. Mounted before /{rxcui}."""
    return {"available": _ai_available()}


def _contains_excluded_ingredient(candidate_rxcui: str, exclude: str) -> bool:
    """True if candidate includes exclude (RxCUI or case-insensitive name)."""
    needle = exclude.strip().lower()
    if not needle:
        return False
    try:
        ingredients = ingredients_for_rxcui(candidate_rxcui)
    except RxNormError:
        return False
    for ing in ingredients:
        if str(ing["rxcui"]) == exclude.strip():
            return True
        if needle in str(ing.get("name") or "").lower():
            return True
    return False


@drugs.get("/analogues/{rxcui}")
def get_analogues(
    rxcui: str,
    mode: AnalogueMode = Query("ingredient"),
    use_ai: bool | None = Query(None),
    exclude_ingredient: str | None = Query(
        None,
        description="Drop candidates whose RxNorm IN list includes this RxCUI or name",
    ),
    principal: Principal = Depends(require("drug:search")),
) -> dict:
    """UC-3/UC-4: analogue candidates ranked by hospital quantity.

    `ingredient` (default): same-active-ingredient SCD/SBD via RxNorm IN.
    `full`: other ingredients in the source RxClass (ATC, else VA / MESHPA).
    `use_ai`: UC-5 Gemini filter on Full only. Ingredient ignores a true value
    once AI is configured. Default is true when a Gemini key is set, false
    when it is not. Explicit true with no key is 409, not an unfiltered list.
    `exclude_ingredient`: physician cart — hide candidates that still contain
    the avoided ingredient (e.g. caffeine RxCUI 1886).
    `orange_book` and `class` are accepted but not implemented.
    Each item includes ``stock_status`` (none/low/normal/high). UC-4 is
    presentation of this ranking, not a different algorithm.
    """
    configured = _ai_available()
    if use_ai is None:
        use_ai = configured
    elif use_ai and not configured:
        raise HTTPException(status_code=409, detail="AI is not configured")
    source = rxcui.strip()
    if not source:
        raise HTTPException(status_code=422, detail="rxcui must not be blank")
    if mode in _UNIMPLEMENTED_MODE:
        raise HTTPException(status_code=422, detail=_UNIMPLEMENTED_MODE[mode])
    try:
        if mode == "full":
            candidates = therapeutic_scd_sbd(source)
        else:
            candidates = related_scd_sbd(source)
        candidates = [row for row in candidates if row["rxcui"] != source]
        if exclude_ingredient and exclude_ingredient.strip():
            exclude = exclude_ingredient.strip()
            workers = min(_NDC_WORKERS, len(candidates)) or 1
            kept: list[dict] = []
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_contains_excluded_ingredient, row["rxcui"], exclude): row
                    for row in candidates
                }
                for fut in as_completed(futures):
                    row = futures[fut]
                    if not fut.result():
                        kept.append(row)
            candidates = kept
    except RxNormError as exc:
        raise HTTPException(status_code=503, detail="rxnorm unavailable") from exc

    ndc_map: dict[str, list[str]] = {}
    workers = min(_NDC_WORKERS, len(candidates))
    if workers:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_ndcs_or_empty, row["rxcui"]): row["rxcui"] for row in candidates}
            for fut in as_completed(futures):
                ndc_map[futures[fut]] = fut.result()

    totals = stock_totals_by_ndc(
        principal,
        [ndc for ndcs in ndc_map.values() for ndc in ndcs],
    )
    items = []
    for row in candidates:
        quantity = sum(totals.get(ndc, 0) for ndc in ndc_map.get(row["rxcui"], []))
        items.append(
            {
                "rxcui": row["rxcui"],
                "tty": row["tty"],
                "name": row["name"],
                **stock_fields(quantity),
            }
        )
    items.sort(key=lambda row: (-row["quantity"], row["name"].lower(), row["rxcui"]))
    items = items[:ANALOGUE_CANDIDATE_LIMIT]
    if mode == "full":
        _log.info("RxClass full rxcui=%s candidates=%s", source, len(items))
    rationale_unavailable = False
    if use_ai and mode == "full" and items:
        items, rationale_unavailable = _filter_full_with_ai(source, items)
    body: dict = {
        "rxcui": source,
        "mode": mode,
        "use_ai": use_ai,
        "rationale_unavailable": rationale_unavailable,
        "items": items,
    }
    # Only echo the filter when the caller asked for it — keeps UC-3/4/5
    # response shape stable for clients/tests that assert exact keys.
    if exclude_ingredient and exclude_ingredient.strip():
        body["exclude_ingredient"] = exclude_ingredient.strip()
    return body


app.include_router(drugs)
app.include_router(drugs, prefix="/api/analogue")
