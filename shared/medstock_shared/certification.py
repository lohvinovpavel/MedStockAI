"""The certification signal — COMP-1 in docs/compliance-usecases.md.

Lives in `shared/` for the same reason `rxnorm.py` does: two processes need it.
`services/ingest/app/certification.py` evaluates these rules when it writes a
row, and `compliance` re-derives the detail from stored findings. One copy, one
place to argue with.

**Red requires a formal government source.** Nothing here can turn a badge red
from a news article or an unverified report — see §4.3 of the use-case doc. An
unconfirmed claim warrants "check this", which is yellow.

**A colour is not enough.** Every finding also declares a *category* (what kind
of problem) and whether it is *transient* (an event that will resolve) or
persistent (a standing property of the product). Both distinctions came out of
measuring real openFDA data:

* 372 of 375 yellows in a 3 000-product sample were `UNAPPROVED_CATEGORY` — a
  permanent attribute. Without separating it, one recall that actually needs
  action is buried under hundreds of badges that will never change.
* `listing_expiration_date` clusters on a single annual date: 70.5% of products
  expire in 2026-12. A "expiring within 90 days" rule on that field turns 70% of
  a formulary amber on the same October morning and is silent the rest of the
  year. That rule is deliberately **not** implemented — see `LISTING_EXPIRED`,
  which fires only on a listing that has actually lapsed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum

# Bump when any threshold or rule below changes. Stored on every row so a colour
# computed months ago can still say which rules produced it.
RULESET_VERSION = "2026.08.2"

# Applies to `marketing_end_date` only — a product-specific wind-down date.
# Explicitly not applied to listing expiry; see the module docstring.
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


class Category(StrEnum):
    """What kind of problem, so a UI can group rather than pile up."""

    LIFECYCLE = "lifecycle"  # is it still a marketed product
    ENFORCEMENT = "enforcement"  # recalls and regulatory action
    APPROVAL = "approval"  # what authority it is sold under
    SUPPLY = "supply"  # can it actually be obtained
    DATA = "data"  # what we could not check


@dataclass(frozen=True)
class Rule:
    category: Category
    severity: Severity
    # Transient = an event with an end: a recall closes, a shortage resolves, a
    # wind-down date passes. Persistent = a standing property that will read the
    # same next year. Only transient findings deserve to interrupt anyone.
    transient: bool
    explain: str


RULES: dict[str, Rule] = {
    "LISTING_EXPIRED": Rule(
        Category.LIFECYCLE, Severity.RED, True, "Listing expiration date has passed"
    ),
    "MARKETING_ENDED": Rule(
        Category.LIFECYCLE, Severity.RED, True, "Marketing end date has passed"
    ),
    "MARKETING_ENDING_SOON": Rule(
        Category.LIFECYCLE,
        Severity.YELLOW,
        True,
        f"Marketing ends within {YELLOW_EXPIRY_WINDOW_DAYS} days",
    ),
    "MARKETING_NOT_STARTED": Rule(
        Category.LIFECYCLE, Severity.YELLOW, True, "Marketing start date is in the future"
    ),
    "RECALL_CLASS_I": Rule(
        Category.ENFORCEMENT, Severity.RED, True, "Class I recall with status Ongoing"
    ),
    "RECALL_CLASS_II": Rule(
        Category.ENFORCEMENT, Severity.YELLOW, True, "Class II recall with status Ongoing"
    ),
    "RECALL_CLASS_III": Rule(
        Category.ENFORCEMENT, Severity.YELLOW, True, "Class III recall with status Ongoing"
    ),
    "SHORTAGE_CURRENT": Rule(
        Category.SUPPLY, Severity.YELLOW, True, "FDA lists this presentation as in shortage"
    ),
    "SHORTAGE_DISCONTINUING": Rule(
        Category.SUPPLY, Severity.YELLOW, True, "Manufacturer has flagged it to be discontinued"
    ),
    "UNAPPROVED_CATEGORY": Rule(
        Category.APPROVAL,
        Severity.YELLOW,
        False,
        "Sold under an unapproved marketing category",
    ),
    "NOT_FINISHED_PRODUCT": Rule(
        Category.APPROVAL, Severity.INFO, False, "Bulk ingredient, not a finished drug product"
    ),
    "DATES_UNKNOWN": Rule(
        Category.DATA, Severity.INFO, False, "Source record carried no lifecycle dates"
    ),
}


_RETIRED_RULE = Rule(
    Category.DATA, Severity.INFO, False, "Finding written by a retired rule version"
)


@dataclass(frozen=True)
class Finding:
    """One reason. `source_ref` is what makes re-running the CronJob an upsert:
    two recalls on the same drug differ by recall number, not by code."""

    code: str
    message: str
    source: str
    source_url: str | None = None
    source_ref: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def rule(self) -> Rule:
        # Rows written by an older ruleset can carry codes this version no
        # longer defines. Degrade to an informational note rather than raising
        # in the middle of rendering a badge.
        return RULES.get(self.code, _RETIRED_RULE)

    @property
    def severity(self) -> Severity:
        return self.rule.severity

    @property
    def category(self) -> Category:
        return self.rule.category

    @property
    def transient(self) -> bool:
        return self.rule.transient


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


@dataclass(frozen=True)
class Shortage:
    status: str | None  # "Current" | "To Be Discontinued" | "Resolved"
    generic_name: str = ""
    therapeutic_category: str = ""
    update_date: str = ""
    raw: dict = field(default_factory=dict)


NDC_DIRECTORY = "openFDA NDC Directory"
ENFORCEMENT = "openFDA Enforcement"
SHORTAGES = "openFDA Drug Shortages"
_NDC_URL = "https://api.fda.gov/drug/ndc.json"
_ENFORCEMENT_URL = "https://api.fda.gov/drug/enforcement.json"
_SHORTAGE_URL = "https://api.fda.gov/drug/shortages.json"

_RECALL_RULES = {
    "class i": "RECALL_CLASS_I",
    "class ii": "RECALL_CLASS_II",
    "class iii": "RECALL_CLASS_III",
}
_SHORTAGE_RULES = {
    "current": "SHORTAGE_CURRENT",
    "to be discontinued": "SHORTAGE_DISCONTINUING",
}


def ndc11(value: str) -> str:
    """Canonical 11-digit NDC, `LLLLLPPPPKK`, zero-padded 5-4-2.

    RxNorm — and therefore `stock_snapshot` — stores this form. openFDA
    publishes the hyphenated one (`0093-9222-05`). Everything here is keyed on
    the 11-digit form so a badge is findable by the NDC inventory already holds.

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
    4-4-2, 5-3-2 or 5-4-1 — the padding erases which. Verified against live
    openFDA: exactly one candidate ever matches.
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
    """openFDA ships `YYYYMMDD`; the shortages feed uses `MM/DD/YYYY`. Anything
    unrecognised is treated as absent rather than raising — one malformed row
    must not fail a whole feed."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            # DTZ007 is suppressed deliberately: a marketing expiry is a calendar
            # date, not an instant. A timezone would invent precision the source
            # does not have.
            return datetime.strptime(text, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    return None


def evaluate(
    *,
    marketing_end_date: date | None = None,
    marketing_start_date: date | None = None,
    listing_expiration_date: date | None = None,
    marketing_category: str | None = None,
    finished: bool | None = None,
    recalls: Sequence[Recall] = (),
    shortages: Sequence[Shortage] = (),
    today: date | None = None,
) -> list[Finding]:
    """Every reason this NDC is not plain green. Order is irrelevant —
    `status_for()` takes the maximum severity, not the first hit."""
    today = today or datetime.now(tz=UTC).date()
    findings: list[Finding] = []

    def add(code: str, message: str, source: str, url: str, ref: str = "", raw: dict | None = None):
        findings.append(
            Finding(code=code, message=message, source=source, source_url=url, source_ref=ref,
                    raw=raw or {})
        )

    # --- lifecycle ---------------------------------------------------------
    # Listing expiry is only ever a *past* signal. Its annual clustering makes a
    # forward-looking window meaningless; see the module docstring.
    if listing_expiration_date is not None and listing_expiration_date < today:
        add(
            "LISTING_EXPIRED",
            f"Listing expired on {listing_expiration_date.isoformat()} "
            f"({(today - listing_expiration_date).days} days ago)",
            NDC_DIRECTORY,
            _NDC_URL,
        )

    if marketing_end_date is not None:
        days = (marketing_end_date - today).days
        if days < 0:
            add(
                "MARKETING_ENDED",
                f"Marketing ended on {marketing_end_date.isoformat()} ({-days} days ago)",
                NDC_DIRECTORY,
                _NDC_URL,
            )
        elif days <= YELLOW_EXPIRY_WINDOW_DAYS:
            add(
                "MARKETING_ENDING_SOON",
                f"Marketing ends on {marketing_end_date.isoformat()} (in {days} days)",
                NDC_DIRECTORY,
                _NDC_URL,
            )

    if marketing_start_date is not None and marketing_start_date > today:
        add(
            "MARKETING_NOT_STARTED",
            f"Marketing does not start until {marketing_start_date.isoformat()}",
            NDC_DIRECTORY,
            _NDC_URL,
        )

    # --- approval ----------------------------------------------------------
    if marketing_category and "unapproved" in marketing_category.lower():
        add(
            "UNAPPROVED_CATEGORY",
            f"Marketing category is '{marketing_category}' — not an approved application",
            NDC_DIRECTORY,
            _NDC_URL,
        )

    if finished is False:
        add(
            "NOT_FINISHED_PRODUCT",
            "Listed as a bulk ingredient rather than a finished drug product",
            NDC_DIRECTORY,
            _NDC_URL,
        )

    # --- enforcement -------------------------------------------------------
    for recall in recalls:
        if not recall.is_open:
            continue  # terminated recalls are history, not a live signal
        code = _RECALL_RULES.get((recall.classification or "").strip().lower())
        if code is None:
            continue
        add(
            code,
            f"{recall.classification} recall ongoing: {recall.reason or 'no reason given'}",
            ENFORCEMENT,
            _ENFORCEMENT_URL,
            recall.recall_number,
            recall.raw,
        )

    # --- supply ------------------------------------------------------------
    for shortage in shortages:
        code = _SHORTAGE_RULES.get((shortage.status or "").strip().lower())
        if code is None:
            continue  # "Resolved" is not a live signal
        label = shortage.generic_name or "this presentation"
        add(
            code,
            f"{label}: FDA shortage status '{shortage.status}'"
            + (f" (updated {shortage.update_date})" if shortage.update_date else ""),
            SHORTAGES,
            _SHORTAGE_URL,
            str(shortage.update_date or ""),
            shortage.raw,
        )

    # --- data quality ------------------------------------------------------
    if marketing_end_date is None and listing_expiration_date is None:
        # Green must never quietly mean "we had no data".
        add("DATES_UNKNOWN", "No marketing or listing date in the source record",
            NDC_DIRECTORY, _NDC_URL)

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


def attention_for(findings: Sequence[Finding]) -> Status:
    """The colour of the *transient* findings only — the ones that represent
    something happening now rather than a permanent attribute.

    This is the number a pharmacist should be interrupted by. A drug that is
    permanently yellow because it is a homeopathic preparation is not news; the
    same drug entering a Class II recall is.
    """
    return status_for([f for f in findings if f.transient])


def signal(findings: Sequence[Finding]) -> dict:
    """The full detail behind a badge, for `GET /status` and the UI.

    `status` is the headline colour, `attention` is the colour of what is
    actually happening, and `categories` lets a UI group reasons instead of
    stacking them.
    """
    by_category: dict[str, str] = {}
    for f in findings:
        worst = by_category.get(f.category)
        if worst is None or _RANK[f.severity] < _RANK[Severity(worst)]:
            by_category[str(f.category)] = str(f.severity)
    return {
        "status": str(status_for(findings)),
        "attention": str(attention_for(findings)),
        "reasons": len(findings),
        "transient": sum(1 for f in findings if f.transient),
        "persistent": sum(1 for f in findings if not f.transient),
        "categories": by_category,
        "codes": sorted({f.code for f in findings}),
    }


_RANK = {Severity.RED: 0, Severity.YELLOW: 1, Severity.INFO: 2}


def ruleset() -> dict:
    """What `GET /ruleset` returns. A tool that will not show you its rules is a
    tool a pharmacist is right to distrust."""
    return {
        "version": RULESET_VERSION,
        "marketing_end_window_days": YELLOW_EXPIRY_WINDOW_DAYS,
        "rules": {
            code: {
                "severity": str(rule.severity),
                "category": str(rule.category),
                "transient": rule.transient,
                "explains": rule.explain,
            }
            for code, rule in sorted(RULES.items())
        },
        "sources": {
            NDC_DIRECTORY: _NDC_URL,
            ENFORCEMENT: _ENFORCEMENT_URL,
            SHORTAGES: _SHORTAGE_URL,
        },
        "notes": [
            "News and unverified reports can never produce red — docs/compliance-usecases.md §4.3",
            (
                "Listing expiry is evaluated only in the past: 70% of products share one "
                "annual expiry date, so a forward-looking window would turn most of a "
                "formulary amber on the same day."
            ),
        ],
    }
