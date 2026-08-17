"""Shared HTTP fetch for every feed module. No auth, no keys — the upstreams
(FDA, RxNorm, CMS NADAC) are all keyless (docs/services.md §7)."""

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

_TIMEOUT = 20.0


def _worth_retrying(exc: BaseException) -> bool:
    """Transport failures and 429/5xx only.

    A 404 means the query matched nothing and a 400 means the query was wrong —
    neither improves by asking again, and openFDA's budget is 1 000 requests a
    day shared across every feed. Retrying a miss three times spent three.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, httpx.TransportError)


@retry(
    retry=retry_if_exception(_worth_retrying),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    # Without this tenacity raises RetryError wrapping the real one, so every
    # `except httpx.HTTPStatusError` in a caller silently never fires.
    reraise=True,
)
def fetch_json(url: str, params: dict | None = None) -> dict:
    resp = httpx.get(url, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()
