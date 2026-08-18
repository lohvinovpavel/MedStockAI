"""Enforcement feed: FDA warning letters into `warning_letter`.

docs/compliance-usecases.md §4.1 — "open enforcement action against a labeler",
marked `verify`. Now verified, and the verification changed the design.

**The page is backed by an export, not a scrape.** The warning-letters listing
posts to a `datatables-data` endpoint that answers with an **XLSX workbook**
(a `PK` zip, not JSON despite the URL). That is a structured export and much
sturdier than parsing the rendered table, so it is what this reads. No Excel
dependency: the sheet is flat, so `zipfile` plus the shared-strings table is
enough.

**What the source will not tell us.** The workbook carries a `Closeout Letter`
column and it is empty on all 1 000 rows, while `Response Letter` is populated
on 128 — the closeout hyperlink does not survive the export. So this feed can
say a letter *was issued* and cannot say whether the action is still open.
Every finding it produces says exactly that and no more; claiming an open
investigation from a source that does not publish closure would be the one
dishonest thing in this module.

**Bounded by design.** The endpoint caps at 1 000 rows however it is paged
(`length`, `items_per_page` and a sort were all tried and all returned 1 000).
Those 1 000 are recent, which is what matters — the newest on the last run was
four months old — and `certification.WARNING_LETTER_WINDOW_DAYS` drops anything
older than three years at finding time anyway.

Most letters are not about drugs: on the last run 382 of 1 000 came from the
Center for Tobacco Products against 194 from CDER. They are all stored and the
exact labeler match sorts it out, which is cheaper and less presumptuous than
guessing FDA's office taxonomy.

Run:  uv run python -m app.warning_letters --dry-run
      uv run python -m app.warning_letters
"""

from __future__ import annotations

import argparse
import html
import io
import re
import sys
import zipfile
from datetime import date

import httpx
from medstock_shared.certification import firm_key
from medstock_shared.db import SessionLocal
from medstock_shared.models import WarningLetter
from sqlalchemy.dialects.postgresql import insert

PAGE = (
    "https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations"
    "/compliance-actions-and-activities/warning-letters"
)
EXPORT = f"{PAGE}/datatables-data"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MedStockAI-ingest/1.0)"}
_TIMEOUT = 90.0

_ROW = re.compile(r"<row[^>]*>(.*?)</row>", re.DOTALL)
_CELL = re.compile(r"<c\b([^>]*)>(.*?)</c>|<c\b([^>]*)/>", re.DOTALL)
_VALUE = re.compile(r"<v>(.*?)</v>", re.DOTALL)
_SHARED = re.compile(r"<t[^>]*>(.*?)</t>", re.DOTALL)
_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


def _fetch() -> bytes:
    resp = httpx.get(EXPORT, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def _as_date(value: str) -> date | None:
    match = _DATE.search(value or "")
    if not match:
        return None
    month, day, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def rows(workbook: bytes) -> list[dict]:
    """Sheet rows as dicts keyed by the header row.

    The `t="s"` attribute is what says a cell holds a shared-string index rather
    than a literal — reading the `<v>` without checking it yields a sheet full
    of integers, which is what the first attempt at this produced.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(workbook))
    except zipfile.BadZipFile:
        return []
    names = archive.namelist()
    sheets = [n for n in names if n.startswith("xl/worksheets/")]
    if not sheets:
        return []
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        shared = [
            html.unescape(s)
            for s in _SHARED.findall(archive.read("xl/sharedStrings.xml").decode("utf-8", "replace"))
        ]

    def cells(row: str) -> list[str]:
        out: list[str] = []
        for match in _CELL.finditer(row):
            attrs = match.group(1) or match.group(3) or ""
            found = _VALUE.search(match.group(2) or "")
            text = found.group(1) if found else ""
            if 't="s"' in attrs and text.isdigit() and int(text) < len(shared):
                text = shared[int(text)]
            out.append(html.unescape(text).strip())
        return out

    raw = _ROW.findall(archive.read(sheets[0]).decode("utf-8", "replace"))
    if not raw:
        return []
    header = cells(raw[0])
    return [dict(zip(header, cells(r), strict=False)) for r in raw[1:]]


def to_letters(records: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple] = set()
    for record in records:
        company = (record.get("Company Name") or "").strip()
        if not company:
            continue
        issued = _as_date(record.get("Letter Issue Date", ""))
        subject = (record.get("Subject") or "").strip()[:500]
        key = (company, issued, subject)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "company_name": company[:300],
                "firm_key": firm_key(company),
                "issue_date": issued,
                "posted_date": _as_date(record.get("Posted Date", "")),
                "issuing_office": (record.get("Issuing Office") or "").strip()[:200] or None,
                "subject": subject or None,
                # Present in the export, unlike closeout — so it is recorded as
                # the one piece of follow-up status the source actually gives.
                "has_response": bool((record.get("Response Letter") or "").strip()),
                "source_url": PAGE,
            }
        )
    return out


def write(letters: list[dict]) -> int:
    if not letters:
        return 0
    with SessionLocal() as session:
        for letter in letters:
            session.execute(
                insert(WarningLetter)
                .values(**letter)
                .on_conflict_do_update(
                    index_elements=["company_name", "issue_date", "subject"],
                    set_={
                        k: v
                        for k, v in letter.items()
                        if k not in ("company_name", "issue_date", "subject")
                    },
                )
            )
        session.commit()
    return len(letters)


def run(dry_run: bool = False) -> int:
    records = rows(_fetch())
    if not records:
        # An empty parse means the export moved, not that FDA stopped writing
        # warning letters. Say so rather than quietly writing nothing.
        print("  0 rows parsed — export format changed?", file=sys.stderr)
        return 0

    letters = to_letters(records)
    newest = max((x["issue_date"] for x in letters if x["issue_date"]), default=None)
    offices = {x["issuing_office"] for x in letters if x["issuing_office"]}
    print(f"  {len(letters)} letters, {len(offices)} issuing offices, newest {newest}")
    if dry_run:
        for letter in letters[:3]:
            print(f"      e.g. {letter['company_name']} — {letter['issue_date']} — {letter['issuing_office']}")
        return 0
    return write(letters)


def main() -> int:
    parser = argparse.ArgumentParser(description="Load FDA warning letters")
    parser.add_argument("--dry-run", action="store_true", help="parse and report, write nothing")
    args = parser.parse_args()
    try:
        written = run(args.dry_run)
    except Exception as exc:  # noqa: BLE001 — a feed failure is not a traceback for ops
        print(f"warning_letters: FAILED {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 1
    print(f"\n{written} letter(s) written. Closeout status is not in this feed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
