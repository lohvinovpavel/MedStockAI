"""Live RxNorm (NLM) client. Keyless, ≤20 req/s requested (docs/services.md §7).

Search and NDC resolution run here so analogue (UC-1) and inventory (stock by
rxcui) share one HTTP shape, one cache, and one rate-limit point. The browser
never calls NLM.
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

RXNORM_BASE = "https://rxnav.nlm.nih.gov/REST"
SELECTABLE_TTY = frozenset({"SCD", "SBD"})
_STRENGTH = re.compile(
    r"(?P<strength>\d+(?:\.\d+)?\s*(?:MG|MCG|UG|G|ML|MEQ|UNT|UNIT|%)"
    r"(?:\s*/\s*\d+(?:\.\d+)?\s*(?:ML|HR|ACTUAT))?)",
    re.IGNORECASE,
)
_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple, tuple[float, Any]] = {}
_SEARCH_TTL = 300.0
_NDC_TTL = 3600.0
_RELATED_TTL = 3600.0
# Cap SCD/SBD siblings so NDC lookups stay inside NLM's requested ≤20 req/s.
ANALOGUE_CANDIDATE_LIMIT = 30
_RELATED_WORKERS = 4


class RxNormError(Exception):
    """Upstream NLM failed or returned an unusable body."""


def parse_strength_and_form(name: str) -> tuple[str | None, str | None]:
    matches = list(_STRENGTH.finditer(name))
    if not matches:
        return None, None
    strength = ", ".join(re.sub(r"\s+", " ", m.group("strength")).strip() for m in matches)
    dose_form = name[matches[-1].end() :].strip(" []") or None
    return strength, dose_form


def _cached(key: tuple, ttl: float, fill):
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
    value = fill()
    with _CACHE_LOCK:
        _CACHE[key] = (now + ttl, value)
    return value


def _get(path: str, params: dict[str, str] | None = None) -> dict:
    try:
        resp = httpx.get(
            f"{RXNORM_BASE}{path}",
            params=params,
            timeout=20.0,
            headers={
                "Accept": "application/json",
                "User-Agent": "MedStockAI/0.1 (formulary; RxNorm lookup)",
            },
        )
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RxNormError(str(exc)) from exc


def _concept(rxcui: str, name: str, tty: str, score: float) -> dict:
    strength, dose_form = parse_strength_and_form(name)
    return {
        "rxcui": str(rxcui),
        "tty": tty,
        "name": name,
        "strength": strength,
        "dose_form": dose_form,
        "_score": score,
    }


def _as_list(value) -> list:
    """RxNav JSON collapses a one-element array to an object."""
    if not value:
        return []
    if isinstance(value, dict):
        return [value]
    return list(value)


def _from_groups(groups, score: float) -> list[dict]:
    items: list[dict] = []
    for group in _as_list(groups):
        if not isinstance(group, dict):
            continue
        tty = group.get("tty")
        for concept in _as_list(group.get("conceptProperties")):
            if not isinstance(concept, dict):
                continue
            concept_tty = concept.get("tty") or tty
            if concept_tty not in SELECTABLE_TTY:
                continue
            rxcui = concept.get("rxcui")
            name = concept.get("name")
            if rxcui and name:
                items.append(_concept(rxcui, name, concept_tty, score))
    return items


def _drugs_json(term: str) -> list[dict]:
    data = _get("/drugs.json", {"name": term})
    groups = (data.get("drugGroup") or {}).get("conceptGroup")
    return _from_groups(groups, score=100.0)


def _properties(rxcui: str) -> dict | None:
    data = _get(f"/rxcui/{rxcui}/properties.json")
    props = data.get("properties") or {}
    if not props.get("rxcui"):
        return None
    return props


def _related_groups(rxcui: str, tty: str) -> list:
    data = _get(f"/rxcui/{rxcui}/related.json?tty={tty}")
    return _as_list((data.get("relatedGroup") or {}).get("conceptGroup"))


def _related_selectable(rxcui: str, score: float) -> list[dict]:
    return _from_groups(_related_groups(rxcui, "SCD+SBD"), score)


def _ingredient_rxcuis(rxcui: str) -> list[str]:
    ids: list[str] = []
    for group in _related_groups(rxcui, "IN"):
        for concept in group.get("conceptProperties") or []:
            rid = concept.get("rxcui")
            if rid:
                ids.append(str(rid))
    return list(dict.fromkeys(ids))


def ingredients_for_rxcui(rxcui: str) -> list[dict[str, str]]:
    """Ingredient (IN) concepts for an SCD/SBD — rxcui + name. Empty on miss."""
    rows: list[dict[str, str]] = []
    for group in _related_groups(str(rxcui).strip(), "IN"):
        for concept in group.get("conceptProperties") or []:
            rid = concept.get("rxcui")
            if not rid:
                continue
            rows.append(
                {
                    "rxcui": str(rid),
                    "name": str(concept.get("name") or rid),
                }
            )
    # Dedupe by rxcui, preserve order.
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        if row["rxcui"] in seen:
            continue
        seen.add(row["rxcui"])
        out.append(row)
    return out


def related_scd_sbd(rxcui: str, limit: int = ANALOGUE_CANDIDATE_LIMIT) -> list[dict]:
    """Same-ingredient SCD/SBD (including branded forms). Excludes `rxcui`.

    Walks related.json on the source, then on each ingredient (IN). Empty list is valid.
    """
    source = str(rxcui).strip()
    cap = max(min(limit, 40), 1)

    def fill() -> list[dict]:
        by_rxcui: dict[str, dict] = {}

        def absorb(items: list[dict]) -> None:
            for item in items:
                if item["rxcui"] == source:
                    continue
                by_rxcui[item["rxcui"]] = item

        absorb(_related_selectable(source, 0.0))
        ingredients = _ingredient_rxcuis(source)
        workers = min(_RELATED_WORKERS, len(ingredients))
        if workers:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_related_selectable, ing, 0.0) for ing in ingredients]
                for fut in as_completed(futures):
                    absorb(fut.result())
        ranked = sorted(
            by_rxcui.values(),
            key=lambda row: (0 if row["tty"] == "SCD" else 1, row["name"].lower(), row["rxcui"]),
        )
        out = []
        for row in ranked[:cap]:
            out.append({k: v for k, v in row.items() if k != "_score"})
        return out

    return _cached(("related_scd_sbd", source, cap), _RELATED_TTL, fill)


_ATC_SOURCES = ("ATC", "ATCPROD", "ATC2")
_FALLBACK_SOURCES = ("VA", "MESHPA")
# Combo products often sit in an ATC class that is only their own ingredients.
# Try a few classes (and VA) before giving up on Full.
_MAX_CLASS_TRIES = 5


def _rxclass_infos(rxcui: str) -> list[dict]:
    data = _get("/rxclass/class/byRxcui.json", {"rxcui": rxcui})
    return _as_list((data.get("rxclassDrugInfoList") or {}).get("rxclassDrugInfo"))


def _ranked_classes(source: str, infos: list[dict]) -> list[dict]:
    """Prefer ATC (product-level ATCPROD first); else VA, then MESHPA.

    VA/MESHPA stay on the list even when ATC exists so Full can fall back if
    the ATC class is emptied by the same-ingredient filter.
    """
    rows: list[dict] = []
    for info in infos:
        item = info.get("rxclassMinConceptItem") or {}
        class_id = item.get("classId")
        rela_source = info.get("relaSource") or ""
        if not class_id or not rela_source:
            continue
        rows.append(
            {
                "classId": str(class_id),
                "relaSource": rela_source,
                "rela": (info.get("rela") or "").strip(),
                "min_cui": str((info.get("minConcept") or {}).get("rxcui") or ""),
            }
        )
    atcprod_ids = {row["classId"] for row in rows if row["relaSource"] == "ATCPROD"}

    def collect(sources: tuple[str, ...]) -> list[dict]:
        return [row for row in rows if row["relaSource"] in sources]

    chosen = collect(_ATC_SOURCES)
    extra: list[dict] = []
    for source_name in _FALLBACK_SOURCES:
        extra.extend(collect((source_name,)))
    if not chosen:
        chosen = extra
        extra = []
    if not chosen:
        return []
    chosen = chosen + extra
    chosen.sort(
        key=lambda row: (
            0 if row["min_cui"] == source else 1,
            0 if row["relaSource"] == "ATCPROD" else 1,
            0 if row["classId"] in atcprod_ids else 1,
            0 if row["relaSource"] in {"ATCPROD", "ATC2"} else 1,
            -len(row["classId"]),
            row["classId"],
        )
    )
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for row in chosen:
        key = (row["classId"], row["relaSource"])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _pick_primary_class(source: str, infos: list[dict]) -> dict | None:
    """Prefer ATC (product-level ATCPROD first); else VA, then MESHPA."""
    ranked = _ranked_classes(source, infos)
    return ranked[0] if ranked else None


def _members_to_concepts(members: list) -> list[dict]:
    items: list[dict] = []
    for member in members:
        concept = member.get("minConcept") or {}
        rxcui = concept.get("rxcui")
        if not rxcui:
            continue
        items.extend(
            _lift_candidate(
                {
                    "rxcui": rxcui,
                    "name": concept.get("name") or "",
                    "tty": concept.get("tty") or "",
                    "score": 0,
                }
            )
        )
    return items


def _class_members(class_id: str, rela_source: str, rela: str) -> list[dict]:
    params: dict[str, str] = {"classId": class_id, "relaSource": rela_source}
    if rela:
        params["rela"] = rela
    ingredient_level = rela_source in {"ATC", "ATC2"}
    if not ingredient_level:
        params["ttys"] = "SCD SBD"
    data = _get("/rxclass/classMembers.json", params)
    members = _as_list((data.get("drugMemberGroup") or {}).get("drugMember"))
    items = _members_to_concepts(members)
    if items or ingredient_level:
        return items
    params.pop("ttys", None)
    data = _get("/rxclass/classMembers.json", params)
    members = _as_list((data.get("drugMemberGroup") or {}).get("drugMember"))
    return _members_to_concepts(members)


def _same_ingredient_rxcuis(source: str) -> set[str]:
    out = {source}
    ingredients = _ingredient_rxcuis(source)
    out.update(ingredients)
    seeds = list(dict.fromkeys([source, *ingredients]))
    workers = min(_RELATED_WORKERS, len(seeds)) or 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_related_selectable, seed, 0.0) for seed in seeds]
        for fut in as_completed(futures):
            for item in fut.result():
                out.add(item["rxcui"])
    return out


def _rank_other_ingredient(members: list[dict], same: set[str], cap: int) -> list[dict]:
    by_rxcui: dict[str, dict] = {}
    for item in members:
        if item["rxcui"] in same:
            continue
        by_rxcui[item["rxcui"]] = item
    ranked = sorted(
        by_rxcui.values(),
        key=lambda row: (0 if row["tty"] == "SCD" else 1, row["name"].lower(), row["rxcui"]),
    )
    out = []
    for row in ranked[:cap]:
        out.append({k: v for k, v in row.items() if k != "_score"})
    return out


def therapeutic_scd_sbd(rxcui: str, limit: int = ANALOGUE_CANDIDATE_LIMIT) -> list[dict]:
    """Other-ingredient SCD/SBD in the source's primary RxClass. Excludes `rxcui`.

    Class is ATC/ATCPROD when present, otherwise VA or MESHPA. If the first
    class is only the source's own ingredients (typical for combinations), try
    the next ATC class and then VA/MESHPA. Same-ingredient preparations belong
    to `related_scd_sbd`. Empty list is valid.
    """
    source = str(rxcui).strip()
    cap = max(min(limit, 40), 1)

    def fill() -> list[dict]:
        classes = _ranked_classes(source, _rxclass_infos(source))
        if not classes:
            return []
        same = _same_ingredient_rxcuis(source)
        for picked in classes[:_MAX_CLASS_TRIES]:
            members = _class_members(picked["classId"], picked["relaSource"], picked["rela"])
            if not members:
                continue
            out = _rank_other_ingredient(members, same, cap)
            if out:
                return out
        return []

    return _cached(("therapeutic_scd_sbd", source, cap), _RELATED_TTL, fill)


def _lift_candidate(candidate: dict) -> list[dict]:
    rxcui = str(candidate.get("rxcui") or "")
    if not rxcui:
        return []
    try:
        score = float(candidate.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    name = candidate.get("name")
    tty = candidate.get("tty")
    if not name or not tty:
        props = _properties(rxcui)
        if not props:
            return []
        name = props.get("name") or ""
        tty = props.get("tty") or ""
    if tty in SELECTABLE_TTY:
        return [_concept(rxcui, name, tty, score)]
    return _related_selectable(rxcui, score)


def _approximate(term: str, max_entries: int) -> list[dict]:
    data = _get(
        "/approximateTerm.json",
        {"term": term, "maxEntries": str(max_entries)},
    )
    raw = [c for c in _as_list((data.get("approximateGroup") or {}).get("candidate")) if isinstance(c, dict)]
    lifted: list[dict] = []
    workers = min(4, len(raw)) or 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_lift_candidate, c) for c in raw]
        for fut in as_completed(futures):
            lifted.extend(fut.result())
    return lifted


def _query_fit(term: str, name: str) -> tuple[int, int, int]:
    """Lower is better. Typed 'aspirin 325' should beat 'aspirin 325 MG / butalbital…'."""
    t = re.sub(r"\s+", " ", term.lower()).strip()
    n = re.sub(r"\s+", " ", name.lower()).strip()
    head = n.split(" / ", 1)[0]
    extras = n.count(" / ")
    if t and (head.startswith(t) or t in head):
        rest = head.replace(t, "", 1).strip()
        return (0, extras, len(rest.split()) if rest else 0)
    if t and t in n:
        return (1, extras, 0)
    return (2, extras, 0)


def search_concepts(term: str, limit: int) -> list[dict]:
    """SCD/SBD hits for a typed name. `in_formulary` is applied by the caller."""

    def fill() -> list[dict]:
        items = _drugs_json(term)
        if not items:
            items = _approximate(term, max_entries=max(limit * 2, 10))
        by_rxcui: dict[str, dict] = {}
        for item in items:
            previous = by_rxcui.get(item["rxcui"])
            if previous is None or item["_score"] > previous["_score"]:
                by_rxcui[item["rxcui"]] = item
        ranked = sorted(
            by_rxcui.values(),
            key=lambda row: (
                -row["_score"],
                *_query_fit(term, row["name"]),
                0 if row["tty"] == "SCD" else 1,
                row["name"],
            ),
        )
        return ranked[: max(limit * 2, limit)]

    return _cached(("search", term.lower(), limit), _SEARCH_TTL, fill)


def apply_formulary(items: list[dict], formulary: set[str], limit: int) -> list[dict]:
    """Formulary hits first; otherwise keep `search_concepts` order (query fit)."""
    decorated = []
    for i, item in enumerate(items):
        row = {k: v for k, v in item.items() if k != "_score"}
        row["in_formulary"] = item["rxcui"] in formulary
        row["_ord"] = i
        decorated.append(row)
    decorated.sort(key=lambda row: (not row["in_formulary"], row["_ord"]))
    out = []
    for row in decorated[:limit]:
        row.pop("_ord", None)
        out.append(row)
    return out


# Live `ndcs.json` is empty for some real SCD/SBD concepts (no current US pack).
# Inventory joins stock on these NDCs; keep this list short and stable.
CURATED_NDCS_WHEN_EMPTY: dict[str, list[str]] = {
    "246461": [  # aspirin 100 MG Oral Tablet — no US NDC in RxNorm
        "00113041178",
        "00904201559",
        "51079024646",
        "68071010030",
    ],
}


def ndcs_for_rxcui(rxcui: str) -> list[str]:
    def fill() -> list[str]:
        data = _get(f"/rxcui/{rxcui}/ndcs.json")
        ndcs = data.get("ndcGroup", {}).get("ndcList", {}).get("ndc") or []
        if isinstance(ndcs, str):
            ndcs = [ndcs]
        out = [str(n) for n in ndcs if n]
        if not out:
            out = list(CURATED_NDCS_WHEN_EMPTY.get(str(rxcui), []))
        return out

    return _cached(("ndcs", rxcui), _NDC_TTL, fill)


def concept_properties(rxcui: str) -> dict | None:
    def fill() -> dict | None:
        props = _properties(rxcui)
        if not props:
            return None
        name = props.get("name") or ""
        strength, dose_form = parse_strength_and_form(name)
        return {
            "rxcui": str(props["rxcui"]),
            "tty": props.get("tty") or "",
            "name": name,
            "strength": strength,
            "dose_form": dose_form,
        }

    return _cached(("props", rxcui), _NDC_TTL, fill)
