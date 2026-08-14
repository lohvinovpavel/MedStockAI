"""The certification traffic light — COMP-1 in docs/compliance-usecases.md.

Lives in `shared/` for the same reason `rxnorm.py` does: two processes need it.
`services/ingest/app/certification.py` evaluates these rules when it writes a
row, and `compliance` re-derives a colour from stored findings when the
thresholds change. One copy, one place to argue with.

**Red requires a formal government source.** Nothing in this module can turn a
badge red from a news article or an unverified report — see §4.3 of the use-case
doc. That is a structural rule, not a preference: an unconfirmed claim warrants
"check this", which is yellow.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum

# Bump when any threshold or rule below changes. Stored on every row so a colour
# computed months ago can still say which rules produced it.
RULESET_VERSION = "2026.08.1"

# One number, one place to defend it. A drug whose marketing or listing lapses
# inside this window is yellow — enough warning to reorder, not so much that
# everything is permanently amber.
YELLOW_EXPIRY_WINDOW_DAYS = 90


class Severity(StrEnum):
    RED = "red"
    YELLOW = "yellow"
    INFO = "info"


class Status(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Finding:
    """One reason. `source_ref` is what makes re-running the CronJob an upsert:
    two recalls on the same drug differ by recall number, not by code."""

    code: str
    severity: Severity
    message: str
    source: str
    source_url: str | None = None
    source_ref: str = ""
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Recall:
    classification: str | None  # "Class I" | "Class II" | "Class III"
    status: str | None  # "Ongoing" | "Terminated" | "Completed"
    recall_number: str = ""
    reason: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return (self.status or "").strip().lower() == "ongoing"


NDC_DIRECTORY = "openFDA NDC Directory"
ENFORCEMENT = "openFDA Enforcement"
_NDC_URL = "https://api.fda.gov/drug/ndc.json"
_ENFORCEMENT_URL = "https://api.fda.gov/drug/enforcement.json"

_RECALL_RULES = {
    "class i": (Severity.RED, "RECALL_CLASS_I"),
    "class ii": (Severity.YELLOW, "RECALL_CLASS_II"),
    "class iii": (Severity.YELLOW, "RECALL_CLASS_III"),
}


def ndc11(value: str) -> str:
    """Canonical 11-digit NDC, `LLLLLPPPPKK`, zero-padded 5-4-2.

    RxNorm — and therefore `stock_snapshot` — stores this form. openFDA
    publishes the hyphenated one (`0093-9222-05`). Everything in this system is
    keyed on the 11-digit form so a badge can be looked up by the NDC inventory
    already holds, which means openFDA values are normalised on the way in.

    Padding forward is unambiguous. Going back is not — see
    `product_ndc_candidates`.
    """
    parts = str(value).strip().split("-")
    if len(parts) == 3:
        return f"{parts[0].zfill(5)}{parts[1].zfill(4)}{parts[2].zfill(2)}"
    if len(parts) == 2:
        return f"{parts[0].zfill(5)}{parts[1].zfill(4)}"
    return str(value).strip()


def product_ndc_candidates(value: str) -> list[str]:
    """The hyphenated `product_ndc` forms an 11-digit NDC could have come from.

    An 11-digit NDC is 5-4-2, but the published 10-digit original may have been
    4-4-2, 5-3-2 or 5-4-1 — the padding erases which. There is no way to tell
    them apart after the fact, so a lookup has to try all the plausible ones;
    verified against live openFDA, exactly one ever matches.
    """
    digits = str(value).strip()
    if len(digits) != 11 or not digits.isdigit():
        return [digits]
    labeler, product = digits[:5], digits[5:9]
    out = []
    if labeler.startswith("0"):
        out.append(f"{labeler[1:]}-{product}")  # published as 4-4-2
    if product.startswith("0"):
        out.append(f"{labeler}-{product[1:]}")  # published as 5-3-2
    out.append(f"{labeler}-{product}")  # published as 5-4-1
    return list(dict.fromkeys(out))


def parse_fda_date(value: str | None) -> date | None:
    """openFDA ships dates as `YYYYMMDD` strings. Anything else is treated as
    absent rather than raising — one malformed row must not fail a whole feed."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            # DTZ007 is suppressed deliberately: a marketing expiry is a calendar
            # date, not an instant. Attaching a timezone would invent precision
            # the source does not have.
            return datetime.strptime(text, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    return None


def evaluate(
    *,
    marketing_end_date: date | None = None,
    listing_expiration_date: date | None = None,
    marketing_category: str | None = None,
    recalls: Sequence[Recall] = (),
    today: date | None = None,
) -> list[Finding]:
    """Every reason this NDC is not plain green. Order is irrelevant —
    `status_for()` takes the maximum severity, not the first hit."""
    today = today or datetime.now(tz=UTC).date()
    horizon = YELLOW_EXPIRY_WINDOW_DAYS
    findings: list[Finding] = []

    for value, label, expired_code, soon_code in (
        (listing_expiration_date, "Listing", "LISTING_EXPIRED", "LISTING_EXPIRING_SOON"),
        (marketing_end_date, "Marketing", "MARKETING_ENDED", "MARKETING_ENDING_SOON"),
    ):
        if value is None:
            continue
        days = (value - today).days
        if days < 0:
            findings.append(
                Finding(
                    code=expired_code,
                    severity=Severity.RED,
                    message=f"{label} expired on {value.isoformat()} ({-days} days ago)",
                    source=NDC_DIRECTORY,
                    source_url=_NDC_URL,
                )
            )
        elif days <= horizon:
            findings.append(
                Finding(
                    code=soon_code,
                    severity=Severity.YELLOW,
                    message=f"{label} expires on {value.isoformat()} (in {days} days)",
                    source=NDC_DIRECTORY,
                    source_url=_NDC_URL,
                )
            )

    if marketing_category and "unapproved" in marketing_category.lower():
        findings.append(
            Finding(
                code="UNAPPROVED_CATEGORY",
                severity=Severity.YELLOW,
                message=f"Marketing category is '{marketing_category}' — not an approved application",
                source=NDC_DIRECTORY,
                source_url=_NDC_URL,
            )
        )

    for recall in recalls:
        if not recall.is_open:
            continue  # terminated recalls are history, not a live signal
        rule = _RECALL_RULES.get((recall.classification or "").strip().lower())
        if rule is None:
            continue
        severity, code = rule
        findings.append(
            Finding(
                code=code,
                severity=severity,
                message=f"{recall.classification} recall ongoing: {recall.reason or 'no reason given'}",
                source=ENFORCEMENT,
                source_url=_ENFORCEMENT_URL,
                source_ref=recall.recall_number,
                raw=recall.raw,
            )
        )

    if marketing_end_date is None and listing_expiration_date is None:
        # Green must never quietly mean "we had no data". This finding carries no
        # severity, but it appears in the evidence list and in the API response.
        findings.append(
            Finding(
                code="DATES_UNKNOWN",
                severity=Severity.INFO,
                message="No marketing or listing expiry date in the source record",
                source=NDC_DIRECTORY,
                source_url=_NDC_URL,
            )
        )

    return findings


def status_for(findings: Sequence[Finding]) -> Status:
    """Highest severity wins. Info-only is still green — an informational
    finding records what we could not check, it does not downgrade the drug."""
    severities = {f.severity for f in findings}
    if Severity.RED in severities:
        return Status.RED
    if Severity.YELLOW in severities:
        return Status.YELLOW
    return Status.GREEN


def ruleset() -> dict:
    """What `GET /ruleset` returns. A tool that will not show you its rules is a
    tool a pharmacist is right to distrust."""
    return {
        "version": RULESET_VERSION,
        "yellow_expiry_window_days": YELLOW_EXPIRY_WINDOW_DAYS,
        "red": {
            "LISTING_EXPIRED": "Listing expiration date is in the past",
            "MARKETING_ENDED": "Marketing end date is in the past",
            "RECALL_CLASS_I": "Class I recall with status Ongoing",
        },
        "yellow": {
            "LISTING_EXPIRING_SOON": f"Listing expires within {YELLOW_EXPIRY_WINDOW_DAYS} days",
            "MARKETING_ENDING_SOON": f"Marketing ends within {YELLOW_EXPIRY_WINDOW_DAYS} days",
            "RECALL_CLASS_II": "Class II recall with status Ongoing",
            "RECALL_CLASS_III": "Class III recall with status Ongoing",
            "UNAPPROVED_CATEGORY": "Marketing category is an unapproved-drug category",
        },
        "info": {"DATES_UNKNOWN": "Source record carried no expiry dates"},
        "sources": {NDC_DIRECTORY: _NDC_URL, ENFORCEMENT: _ENFORCEMENT_URL},
        "note": "News and unverified reports can never produce red — see docs/compliance-usecases.md §4.3",
    }
