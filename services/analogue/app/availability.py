"""C5 local availability overlay. Ranking is produced by C3/C4; this only annotates."""

from __future__ import annotations

from collections.abc import Iterable

from medstock_shared.geo import haversine_km


def overlay_availability(
    items: list[dict],
    ndc_map: dict[str, list[str]],
    qty_by_fac_ndc: dict[tuple[int, str], int],
    facilities: Iterable[object],
    origin,
    operated_only: bool,
) -> list[dict]:
    """Attach `availability` to each analogue row without reordering.

    `origin` and each facility need `.id`, `.name`, `.lat`, `.lon`, `.operated`.
    """
    fac_by_id = {int(f.id): f for f in facilities}
    out: list[dict] = []
    origin_id = int(origin.id)
    for row in items:
        annotated = dict(row)
        ndcs = ndc_map.get(row["rxcui"], [])
        here = _qty_at(origin_id, ndcs, qty_by_fac_ndc)
        nearest = _nearest(
            origin,
            ndcs,
            qty_by_fac_ndc,
            fac_by_id,
            operated_only=operated_only,
        )
        annotated["availability"] = {
            "facility_id": origin_id,
            "quantity": here,
            "unit": "packs",
            "nearest_with_stock": nearest,
        }
        out.append(annotated)
    return out


def _qty_at(facility_id: int, ndcs: list[str], qty_by_fac_ndc: dict[tuple[int, str], int]) -> int:
    return sum(int(qty_by_fac_ndc.get((facility_id, ndc), 0)) for ndc in ndcs)


def _nearest(
    origin,
    ndcs: list[str],
    qty_by_fac_ndc: dict[tuple[int, str], int],
    fac_by_id: dict[int, object],
    operated_only: bool,
) -> dict | None:
    origin_id = int(origin.id)
    if origin.lat is None or origin.lon is None:
        return None
    best: dict | None = None
    best_km: float | None = None
    for fid, fac in fac_by_id.items():
        if fid == origin_id:
            continue
        if operated_only and not bool(fac.operated):
            continue
        qty = _qty_at(fid, ndcs, qty_by_fac_ndc)
        if qty <= 0:
            continue
        if fac.lat is None or fac.lon is None:
            continue
        km = round(
            haversine_km(
                float(origin.lat), float(origin.lon), float(fac.lat), float(fac.lon)
            ),
            1,
        )
        if best_km is None or km < best_km or (km == best_km and fid < int(best["facility_id"])):
            best_km = km
            best = {
                "facility_id": fid,
                "name": fac.name,
                "quantity": qty,
                "distance_km": km,
            }
    return best
