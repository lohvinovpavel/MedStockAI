"""Formulary CSV parse (B6) and local RxCUI → NDC resolution for B3/B2.

Name is never stored. Canonical names come from RxNorm or the demo shelf at
read time so a stale CSV cannot rename a drug.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable

from .demo_shelf import DASHBOARD_SHELF

MAX_FORMULARY_BYTES = 5 * 1024 * 1024
MAX_FORMULARY_ROWS = 10_000

_RXCUI_RE = re.compile(r"^\d+$")


def parse_formulary_csv(text: str) -> tuple[list[str], list[dict]]:
    """Return `(rxcuis_in_file_order, rejected)`.

    Validates the whole file before the caller writes. Duplicates keep the
    first occurrence and report the rest. Blank lines and non-numeric rxcuis
    are rejected, not fatal — the API still imports the valid rows.
    """
    if not text.strip():
        raise ValueError("empty_file")

    stream = io.StringIO(text)
    first = stream.readline()
    if not first:
        raise ValueError("empty_file")
    header_cells = [c.strip().lower().lstrip("\ufeff") for c in next(csv.reader([first]))]
    if "rxcui" not in header_cells:
        raise ValueError("unrecognised_header")

    reader = csv.reader(stream)
    rxcuis: list[str] = []
    seen: set[str] = set()
    rejected: list[dict] = []
    # Header is line 1.
    line_no = 1
    data_rows = 0
    for raw in reader:
        line_no += 1
        if not raw or all(not str(c).strip() for c in raw):
            rejected.append({"line": line_no, "rxcui": "", "reason": "blank_line"})
            data_rows += 1
            if data_rows > MAX_FORMULARY_ROWS:
                raise ValueError("too_many_rows")
            continue
        data_rows += 1
        if data_rows > MAX_FORMULARY_ROWS:
            raise ValueError("too_many_rows")
        row = {header_cells[i]: (raw[i].strip() if i < len(raw) else "") for i in range(len(header_cells))}
        rxcui = row.get("rxcui", "").strip()
        if not rxcui or not _RXCUI_RE.fullmatch(rxcui):
            rejected.append({"line": line_no, "rxcui": rxcui, "reason": "rxcui_not_numeric"})
            continue
        if rxcui in seen:
            rejected.append({"line": line_no, "rxcui": rxcui, "reason": "duplicate_in_file"})
            continue
        seen.add(rxcui)
        rxcuis.append(rxcui)
    return rxcuis, rejected


def shelf_ndcs_for_rxcuis(rxcuis: Iterable[str]) -> dict[str, list[str]]:
    """Demo-shelf NDC overlay so B3/C5/in_formulary do not depend on live RxNorm."""
    wanted = {str(r) for r in rxcuis}
    out: dict[str, list[str]] = {r: [] for r in wanted}
    for item in DASHBOARD_SHELF:
        rxcui = str(item.get("rxcui") or "")
        if rxcui in out:
            ndc = str(item["ndc"])
            if ndc not in out[rxcui]:
                out[rxcui].append(ndc)
    return {k: v for k, v in out.items() if v}


def shelf_name_for_rxcui(rxcui: str) -> str | None:
    for item in DASHBOARD_SHELF:
        if str(item.get("rxcui") or "") == str(rxcui):
            return str(item["name"])
    return None
