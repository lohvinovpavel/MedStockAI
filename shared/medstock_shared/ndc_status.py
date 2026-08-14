"""RxNorm NDC status — the second formal source, and the one that answers for
drugs openFDA does not list (COMP-2).

The openFDA NDC Directory carries *currently marketed* products only. Measured
against 18 real shelf NDCs it could not answer for, an on-demand query to the
same feed resolved 0, the SPL label endpoint 3, and this endpoint all 18 — with
an ACTIVE/OBSOLETE verdict and the date range behind it. An NDC the directory
has dropped is usually not a mystery; it is obsolete, and RxNorm says so.

Keyless, NLM-published, ~20 req/s requested. Being a government source is what
lets it set a red badge at all (docs/compliance-usecases.md §4.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

RXNAV_URL = "https://rxnav.nlm.nih.gov/REST/ndcstatus.json"
_TIMEOUT = 10.0


@dataclass(frozen=True)
class NdcStatus:
    """`status` is RxNorm's: ACTIVE, OBSOLETE, ALIEN or UNKNOWN."""

    status: str
    start_date: str = ""  # YYYYMM
    end_date: str = ""  # YYYYMM
    active_rxcui: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def is_obsolete(self) -> bool:
        return self.status.strip().upper() == "OBSOLETE"

    @property
    def is_active(self) -> bool:
        return self.status.strip().upper() == "ACTIVE"


@retry(
    retry=retry_if_exception_type(httpx.TransportError),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _get(ndc: str) -> dict | None:
    response = httpx.get(RXNAV_URL, params={"ndc": ndc}, timeout=_TIMEOUT)
    if response.status_code != 200:
        return None
    return response.json()


def fetch_ndc_status(ndc: str) -> NdcStatus | None:
    """One NDC, one lookup. `None` means RxNorm had nothing to say — which is
    itself worth recording, rather than being read as "fine"."""
    try:
        body = _get(ndc)
    except httpx.HTTPError:
        return None
    if not body:
        return None

    status = (body.get("ndcStatus") or {})
    label = str(status.get("status") or "").strip()
    if not label:
        return None

    history = status.get("ndcHistory") or []
    first = history[0] if history else {}
    return NdcStatus(
        status=label,
        start_date=str(first.get("startDate") or ""),
        end_date=str(first.get("endDate") or ""),
        active_rxcui=str(first.get("activeRxcui") or status.get("rxcui") or ""),
        raw=body,
    )
