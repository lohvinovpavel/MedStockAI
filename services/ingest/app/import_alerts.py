"""Import certification feed: FDA Import Alert Red Lists into `import_alert`.

docs/compliance-usecases.md §4.1 and `OPEN` item 3, which accepts the fragility
of HTML parsing because no JSON API exposes import alerts and covering *import*
certification without them is not possible.

Alerts 66-40 (drugs from firms that have not met CGMPs) and 66-41 (unapproved
drugs). "Red List" is FDA's term for detention without physical examination.

**The doc marked this source `verify`. It is now verified**, and the structure
turned out to be different from the guess:

* `ialist.html` is the index. It carries the alert *number* in a `<td>` and the
  detail page in a sibling `<td>`, so the number-to-page mapping is read rather
  than hardcoded — the numeric page ids are internal and have already drifted
  (66-40 is `importalert_189.html`, not `_190`).
* The detail pages contain **no `<table>` at all** despite being ~2 MB. Firms are
  `<div class="div-name floatleft">` blocks, 466 of them on 66-40, each followed
  by a published date and an address line whose last comma-separated field is the
  country.

That specificity is the fragility: any of it can change without warning, and
when it does this returns zero firms rather than wrong ones. `--dry-run` exists
so a human can see what the parser found before it is written anywhere.

Run:  uv run python -m app.import_alerts --dry-run
      uv run python -m app.import_alerts
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import UTC, date, datetime

import httpx
from medstock_shared.certification import firm_key
from medstock_shared.db import SessionLocal
from medstock_shared.models import ImportAlert
from sqlalchemy.dialects.postgresql import insert

BASE = "https://www.accessdata.fda.gov/cms_ia/"
INDEX = f"{BASE}ialist.html"
ALERTS = ("66-40", "66-41")

# accessdata rejects an empty UA. Identifying the caller is also the polite
# thing to do when scraping a government site on a schedule.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MedStockAI-ingest/1.0)"}
_TIMEOUT = 90.0

# <td>66-40</td> … <td><a href="importalert_189.html">
_INDEX_ROW = re.compile(
    r"<td[^>]*>\s*(\d{2}-\d{2})\s*</td>.*?<a[^>]+href=\"(importalert_\d+\.html)\"",
    re.IGNORECASE | re.DOTALL,
)
# One firm: name, published date, then the address line.
_FIRM = re.compile(
    r"<div class=\"div-name floatleft\">(?P<name>.*?)</div>\s*"
    r"<div class=\"div-name floatright\">(?P<published>.*?)</div>\s*"
    r"<div class=\"clear\">(?P<address>.*?)</div>",
    re.IGNORECASE | re.DOTALL,
)
_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").strip()


def _squash(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ,")


def _country(address: str) -> str:
    """The country from an address line, or "" rather than a guess.

    FDA writes the country in capitals at the end, but the separator is not
    reliable: "…, Yerevan, ARMENIA" has a comma, "AM-KT ARMENIA" and "Buenos
    Aires ARGENTINA" do not. So the country is the trailing run of all-caps
    tokens, minus any leading region code — taking the last comma-separated
    field instead yields "AM-KT ARMENIA" and inflates a 160-country list into a
    529-"country" one.

    Multi-word countries (UNITED KINGDOM, SOUTH KOREA) survive because the whole
    trailing caps run is taken, not just the last token.
    """
    tokens = _squash(address).split()
    trailing: list[str] = []
    for token in reversed(tokens):
        stripped = token.strip(",.")
        if stripped and stripped == stripped.upper() and any(c.isalpha() for c in stripped):
            trailing.insert(0, stripped)
        else:
            break
    # A leading "AM-KT" or "CA" is a state/province code, not part of the name.
    while len(trailing) > 1 and ("-" in trailing[0] or len(trailing[0]) <= 3):
        trailing.pop(0)
    return " ".join(trailing)


def _get(url: str) -> str:
    resp = httpx.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _published(fragment: str) -> date | None:
    match = _DATE.search(_text(fragment))
    if not match:
        return None
    month, day, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def alert_pages(index_html: str) -> dict[str, str]:
    """Alert number -> detail page, read from the index rather than hardcoded."""
    return {number: page for number, page in _INDEX_ROW.findall(index_html)}


def firms(page_html: str, alert_number: str, source_url: str) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for match in _FIRM.finditer(page_html):
        name = _squash(_text(match.group("name")))
        if not name:
            continue
        key = firm_key(name)
        # The same firm can appear twice with different capitalisation
        # ("BABIKIAN HEALTHCARE PRODUCTS" and "Babikian Healthcare Products,
        # CJSC" are both on 66-40). The natural key is the raw name, so both
        # rows are kept — but a duplicate *within one page* is a parse artefact.
        if name in seen:
            continue
        seen.add(name)
        address = _squash(_text(match.group("address")))
        country = _country(address)
        rows.append(
            {
                "alert_number": alert_number,
                "firm_name": name,
                "firm_key": key,
                "country": country or None,
                "address": address or None,
                "listed_at": _published(match.group("published")),
                "source_url": source_url,
            }
        )
    return rows


def write(rows: list[dict]) -> int:
    if not rows:
        return 0
    with SessionLocal() as session:
        for row in rows:
            session.execute(
                insert(ImportAlert)
                .values(**row)
                .on_conflict_do_update(
                    index_elements=["alert_number", "firm_name"],
                    set_={
                        k: v for k, v in row.items() if k not in ("alert_number", "firm_name")
                    },
                )
            )
        session.commit()
    return len(rows)


def run(dry_run: bool = False) -> int:
    index = _get(INDEX)
    pages = alert_pages(index)
    total = 0
    for alert in ALERTS:
        page = pages.get(alert)
        if not page:
            print(f"  {alert}: not found in the index — layout changed?", file=sys.stderr)
            continue
        url = f"{BASE}{page}"
        rows = firms(_get(url), alert, url)
        if not rows:
            # Zero firms on a live alert means the parser broke, not that the
            # FDA emptied the list. Say so rather than quietly writing nothing.
            print(f"  {alert}: 0 firms parsed from {page} — layout changed?", file=sys.stderr)
            continue
        countries = len({r["country"] for r in rows if r["country"]})
        print(f"  {alert}: {len(rows)} firms across {countries} countries ({page})")
        if dry_run:
            for row in rows[:3]:
                print(f"      e.g. {row['firm_name']} — {row['country']} — {row['listed_at']}")
        else:
            total += write(rows)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape FDA Import Alert Red Lists")
    parser.add_argument("--dry-run", action="store_true", help="parse and report, write nothing")
    args = parser.parse_args()
    try:
        written = run(args.dry_run)
    except Exception as exc:  # noqa: BLE001 — a feed failure is not a traceback for ops
        print(f"import_alerts: FAILED {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 1
    print(f"\n{written} firm listing(s) written at {datetime.now(tz=UTC).isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
