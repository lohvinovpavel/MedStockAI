"""Shared HTTP fetch for every feed module. No auth, no keys — the three
upstreams (FDA, RxNorm, CMS NADAC) are all keyless (docs/services.md §7)."""

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

_TIMEOUT = 20.0


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def fetch_json(url: str, params: dict | None = None) -> dict:
    resp = httpx.get(url, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()
