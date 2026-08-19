"""Informal signal feed: GDELT press mentions into `news_signal`.

docs/compliance-usecases.md §4.2 — GDELT is the recommended default: a global
news index, keyless, filterable by date and domain. §4.3 is the rule that
governs what any of it is allowed to do:

    **News raises Yellow and attaches the article; only a government source
    sets Red.**

That is structural, not a preference. An article is an unverified claim about a
third party, and acting on one as fact means the system can tell a pharmacist a
drug is uncertified because a blog said so. Yellow means "check this", which is
exactly what an unconfirmed report warrants. The severity lives in
`certification.RULES["NEWS_SIGNAL"]` where it cannot be argued with per-article.

Queried per drug **name**, not per NDC, because no newsroom writes an NDC. The
NDC is carried on the row so a badge can join, and the query term is stored
beside it so a reader can judge how loose the match was — "heparin" finds
articles about heparin; it also finds articles about a heparin lawsuit in
another country, and the pharmacist should be able to see which they are
looking at.

Run:  uv run python -m app.news --drug "heparin" --ndc 00641040025
      uv run python -m app.news --shelf
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx
from medstock_shared.db import SessionLocal, iter_hospitals
from medstock_shared.models import Drug, NewsSignal, StockSnapshot
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ._source import fetch_json

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_NEWS = "https://news.google.com/rss/search"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MedStockAI-ingest/1.0)"}

# A recall reported eighteen months ago is history, not a signal. The
# certification badge is about what a pharmacist should check today.
WINDOW_DAYS = 30
MAX_RECORDS = 25

# Without a topic qualifier "heparin" returns commodity-price pieces and
# unrelated chemistry. These are the words that make a hit worth a pharmacist's
# attention; anything matching none of them is not about drug safety.
TOPIC = '(recall OR contamination OR "FDA warning" OR shortage OR adulterated OR counterfeit)'


# GDELT allows roughly one query per five seconds per IP and answers 429 rather
# than queueing. Two back-to-back queries is enough to trip it — measured, not
# assumed. The retry helper's exponential backoff tops out at 10s and starts
# *after* the first failure, so pacing before the call is what actually keeps
# this under the limit. Offline, nobody is waiting, so the wait is free.
_PACE_SECONDS = 6.0
_last_call = 0.0


def _pace() -> None:
    global _last_call
    wait = _PACE_SECONDS - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _gdelt(drug: str, since: datetime) -> list[dict]:
    body = fetch_json(
        GDELT,
        {
            "query": f'"{drug}" {TOPIC} sourcelang:english',
            "mode": "artlist",
            "format": "json",
            "maxrecords": MAX_RECORDS,
            "startdatetime": since.strftime("%Y%m%d%H%M%S"),
        },
    )
    return [
        {
            "title": a.get("title"),
            "url": a.get("url"),
            "domain": a.get("domain"),
            "published_at": _gdelt_date(a.get("seendate")),
        }
        for a in (body.get("articles") or [])
    ]


def _google_news(drug: str, since: datetime) -> list[dict]:
    """§4.2's keyless fallback. Best-effort by the doc's own description.

    **The window has to be applied here.** Google News RSS ignores date
    qualifiers and will happily return a 2017 story about contaminated heparin
    alongside this month's — which as a badge signal would be a nine-year-old
    article presented as current.
    """
    resp = httpx.get(
        GOOGLE_NEWS,
        params={"q": f"{drug} recall OR contamination OR shortage", "hl": "en-US",
                "gl": "US", "ceid": "US:en"},
        headers=_HEADERS,
        timeout=30.0,
        follow_redirects=True,
    )
    resp.raise_for_status()

    out: list[dict] = []
    for item in re.findall(r"(?s)<item>(.*?)</item>", resp.text)[: MAX_RECORDS * 2]:

        def field(tag: str, blob: str = item) -> str:
            match = re.search(rf"(?s)<{tag}[^>]*>(.*?)</{tag}>", blob)
            return html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip() if match else ""

        published = _rss_date(field("pubDate"))
        if published is None or published < since:
            continue
        out.append(
            {
                "title": field("title"),
                "url": field("link"),
                "domain": field("source") or None,
                "published_at": published,
            }
        )
    return out[:MAX_RECORDS]


def search(drug: str, window_days: int = WINDOW_DAYS) -> list[dict]:
    """GDELT, falling back to Google News RSS.

    GDELT is §4.2's recommended default and stays the first choice. It answers
    429 from some networks regardless of pacing or user agent — verified here
    against a bare query — and a compliance feed that silently produces nothing
    because one index is unreachable is worse than a best-effort second source
    the doc already sanctions. Whichever answers, the severity is the same:
    yellow, never red.
    """
    _pace()
    since = datetime.now(tz=UTC) - timedelta(days=window_days)
    try:
        return _gdelt(drug, since)
    except (httpx.HTTPError, ValueError) as exc:
        print(f"      gdelt unavailable ({type(exc).__name__}); falling back to RSS")
        return _google_news(drug, since)


def _gdelt_date(value: str | None) -> datetime | None:
    """GDELT stamps are `20260817T134500Z`."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _rss_date(value: str) -> datetime | None:
    """RFC 2822, as RSS uses: `Sun, 22 Feb 2026 19:15:54 GMT`."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def rows_for(drug: str, ndc: str | None, articles: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for article in articles:
        url = str(article.get("url") or "").strip()
        headline = str(article.get("title") or "").strip()
        if not url or not headline:
            continue
        rows.append(
            {
                "ndc": ndc,
                "query_term": drug,
                "headline": headline[:500],
                "url": url,
                "domain": (str(article.get("domain") or "").strip() or None),
                "published_at": article.get("published_at"),
            }
        )
    return rows


def shelf() -> list[tuple[str, str]]:
    """(drug name, ndc) for what is actually stocked. Querying the whole
    formulary would spend a lot of requests on drugs nobody holds."""
    with SessionLocal() as session:
        all_shelf: set[tuple[str, str]] = set()
        for _ in iter_hospitals(session):
            rows = session.execute(
                select(StockSnapshot.ndc, Drug.name)
                .join(Drug, Drug.ndc == StockSnapshot.ndc)
                .distinct()
            ).all()
            for ndc, name in rows:
                if name and ndc:
                    all_shelf.add((str(name), str(ndc)))
        return list(all_shelf)


def write(rows: list[dict]) -> int:
    if not rows:
        return 0
    with SessionLocal() as session:
        for row in rows:
            session.execute(
                insert(NewsSignal)
                .values(**row)
                .on_conflict_do_update(
                    index_elements=["url"],
                    set_={k: v for k, v in row.items() if k != "url"},
                )
            )
        session.commit()
    return len(rows)


def run(targets: list[tuple[str, str | None]]) -> int:
    written = 0
    for drug, ndc in targets:
        try:
            articles = search(drug)
        except Exception as exc:  # noqa: BLE001 — one bad query must not end the run
            print(f"  {drug}: FAILED {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)
            continue
        rows = rows_for(drug, ndc, articles)
        written += write(rows)
        print(f"  {drug}: {len(rows)} article(s)")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Load GDELT press mentions into news_signal")
    parser.add_argument("--drug", nargs="*", default=[])
    parser.add_argument("--ndc", default=None, help="NDC to attach when a single --drug is given")
    parser.add_argument("--shelf", action="store_true", help="every stocked drug")
    args = parser.parse_args()

    targets: list[tuple[str, str | None]] = [(d, args.ndc) for d in args.drug]
    if args.shelf:
        targets += [(name, ndc) for name, ndc in shelf()]
    if not targets:
        print("nothing to do: pass --drug or --shelf", file=sys.stderr)
        return 2

    written = run(targets)
    print(f"\n{written} article(s). News can raise yellow and never red — §4.3.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
