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

import re
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
    # COMP-2: what on-demand exploration can add for a drug openFDA does not list.
    "NDC_OBSOLETE": Rule(
        Category.LIFECYCLE, Severity.RED, True, "RxNorm lists this NDC as obsolete"
    ),
    "NDC_ACTIVE_UNLISTED": Rule(
        Category.DATA,
        Severity.INFO,
        False,
        "Not in the FDA NDC Directory, but RxNorm lists the NDC active",
    ),
    "NDC_UNRESOLVED": Rule(
        Category.DATA, Severity.INFO, False, "No formal source recognised this NDC"
    ),
    # Import alerts. Yellow rather than red, and persistent rather than
    # transient: detention without physical examination is a standing regulatory
    # posture on a *manufacturer*, not a defect found in this product. It says
    # "check where this came from", which is what yellow means.
    "IMPORT_ALERT_GMP": Rule(
        Category.ENFORCEMENT,
        Severity.YELLOW,
        False,
        "Labeler is on FDA Import Alert 66-40 — detained for CGMP failure",
    ),
    "IMPORT_ALERT_UNAPPROVED": Rule(
        Category.ENFORCEMENT,
        Severity.YELLOW,
        False,
        "Labeler is on FDA Import Alert 66-41 — detained as an unapproved drug",
    ),
    "WARNING_LETTER": Rule(
        Category.ENFORCEMENT,
        Severity.YELLOW,
        True,
        "FDA issued a warning letter to this labeler",
    ),
    # News. §4.3 makes this structural: an article is an unverified claim about
    # a third party, so it can raise yellow and never red. Acting on one as fact
    # would let the system call a drug uncertified because a blog said so.
    "NEWS_SIGNAL": Rule(
        Category.ENFORCEMENT,
        Severity.YELLOW,
        True,
        "Recent press reporting mentions this drug — unverified, check the source",
    ),
}

# Corporate suffixes carry no identity: "Aruba Aloe Balm N.V." and "Aruba Aloe
# Balm NV" are one firm. Everything else is left alone on purpose — see firm_key.
_FIRM_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "co", "corp", "corporation",
    "plc", "gmbh", "ag", "sa", "srl", "spa", "bv", "nv", "cjsc", "jsc", "ojsc",
    "pty", "pte", "sdn", "bhd", "kk", "as", "ab", "oy", "aps", "kft", "doo",
    "sas", "sarl", "lp", "llp", "pvt", "private",
}


def firm_key(name: str) -> str:
    """Normalise a firm name for **exact** matching, and no more than that.

    Casefold, drop punctuation, drop trailing corporate suffixes, collapse
    whitespace. Deliberately *not* fuzzy: no edit distance, no token subset, no
    substring containment.

    The asymmetry matters. A missed import alert shows up as a drug with no
    finding, which is the state everything not on an alert is already in. A
    false match publicly accuses a named manufacturer of being detained at the
    border over a product that has nothing to do with them. Under-matching is
    recoverable and over-matching is a libel, so this errs hard toward missing
    and the finding names the matched firm so a human can check it.
    """
    # Periods vanish rather than becoming spaces: "N.V." and "NV" are the same
    # suffix, and splitting the first into "n v" would leave two tokens that
    # match no suffix at all — so the punctuated spelling, which is the common
    # one on FDA listings, would never match the unpunctuated one. That was the
    # first thing these tests caught.
    lowered = str(name).casefold().replace(".", "")
    cleaned = re.sub(r"[^a-z0-9\s]", " ", lowered)
    tokens = [t for t in cleaned.split() if t]
    # Never strip to nothing. A firm named "Limited" would otherwise normalise
    # to the empty string and match every other name that did the same.
    while len(tokens) > 1 and tokens[-1] in _FIRM_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


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


@dataclass(frozen=True)
class WarningAction:
    """One FDA warning letter, already matched to this NDC's labeler.

    No `closed` field, deliberately. FDA's export publishes a Closeout Letter
    column and leaves it empty on every row, so whether the action is still
    open is simply not in the data — and a field for it would invite a caller
    to assume one.
    """

    company_name: str
    issue_date: date | None = None
    issuing_office: str = ""
    subject: str = ""
    source_url: str = ""


@dataclass(frozen=True)
class AlertListing:
    """One Red List entry, already matched to this NDC's labeler."""

    alert_number: str  # "66-40" | "66-41"
    firm_name: str
    country: str = ""
    listed_at: date | None = None
    source_url: str = ""


@dataclass(frozen=True)
class NewsItem:
    """One press mention. Yellow at most, always — §4.3."""

    headline: str
    url: str = ""
    domain: str = ""
    published_at: datetime | None = None


NDC_DIRECTORY = "openFDA NDC Directory"
ENFORCEMENT = "openFDA Enforcement"
SHORTAGES = "openFDA Drug Shortages"
RXNORM = "RxNorm NDC Status (NLM)"
IMPORT_ALERTS = "FDA Import Alerts (DWPE)"
WARNING_LETTERS = "FDA Warning Letters"
# A letter from six years ago is history, not something to act on today.
WARNING_LETTER_WINDOW_DAYS = 3 * 365
NEWS = "Press reporting"
_IMPORT_ALERT_RULES = {"66-40": "IMPORT_ALERT_GMP", "66-41": "IMPORT_ALERT_UNAPPROVED"}
_NDC_URL = "https://api.fda.gov/drug/ndc.json"
_ENFORCEMENT_URL = "https://api.fda.gov/drug/enforcement.json"
_SHORTAGE_URL = "https://api.fda.gov/drug/shortages.json"
_RXNAV_URL = "https://rxnav.nlm.nih.gov/REST/ndcstatus.json"

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
    parts = [p for p in str(value).strip().split("-") if p]
    if len(parts) == 3:
        return f"{parts[0].zfill(5)}{parts[1].zfill(4)}{parts[2].zfill(2)}"
    if len(parts) == 2:
        raise ValueError(
            f"NDC {value!r} has no package segment; an 11-digit NDC needs labeler-product-package"
        )
    val = str(value).strip()
    if not val:
        raise ValueError("Empty NDC")
    if len(val) == 9 and val.isdigit():
        raise ValueError(
            f"NDC {value!r} has no package segment; an 11-digit NDC needs 11 digits (labeler-product-package)"
        )
    return val


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
    import_alerts: Sequence[AlertListing] = (),
    warning_letters: Sequence[WarningAction] = (),
    news: Sequence[NewsItem] = (),
    ndc_status: object | None = None,
    in_directory: bool = True,
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

    # --- import certification (§4.1) ---------------------------------------
    # The caller matched these to this NDC's labeler; `firm_key` documents why
    # the match is exact-only. The firm is named in the message so a pharmacist
    # can check the claim rather than take it, which matters more here than
    # anywhere else in this module: this one is about a company, not a product.
    for listing in import_alerts:
        code = _IMPORT_ALERT_RULES.get(listing.alert_number.strip())
        if code is None:
            continue
        where = f", {listing.country}" if listing.country else ""
        since = f" since {listing.listed_at.isoformat()}" if listing.listed_at else ""
        add(
            code,
            f"Labeler matches '{listing.firm_name}'{where} on FDA Import Alert "
            f"{listing.alert_number}{since} — detained without physical examination",
            IMPORT_ALERTS,
            listing.source_url,
            listing.alert_number,
        )

    # --- open enforcement (§4.1) -------------------------------------------
    # Yellow, and transient because a warning letter is an event that resolves.
    #
    # The message says a letter *was issued* and never that an investigation is
    # open, because FDA's feed does not publish closeout status — see
    # models.WarningLetter. Claiming "open" from a source that cannot say so
    # would be the one dishonest finding in this module.
    for letter in warning_letters:
        if letter.issue_date is not None:
            age = (today - letter.issue_date).days
            if age > WARNING_LETTER_WINDOW_DAYS or age < 0:
                continue
        when_issued = f" on {letter.issue_date.isoformat()}" if letter.issue_date else ""
        office = f" ({letter.issuing_office})" if letter.issuing_office else ""
        topic = f" — {letter.subject}" if letter.subject else ""
        add(
            "WARNING_LETTER",
            f"FDA warning letter to '{letter.company_name}'{when_issued}{office}{topic}. "
            "Closeout status is not published in this feed; check FDA before "
            "treating it as resolved.",
            WARNING_LETTERS,
            letter.source_url,
        )

    # --- news (§4.2, and §4.3 for why it stops at yellow) -------------------
    for item in news:
        when = f" ({item.published_at.date().isoformat()})" if item.published_at else ""
        source = f" — {item.domain}" if item.domain else ""
        add(
            "NEWS_SIGNAL",
            f"{item.headline}{source}{when}",
            NEWS,
            item.url,
        )

    # --- RxNorm, the second formal source (COMP-2) --------------------------
    # Duck-typed rather than imported: `ndc_status.py` reaches the network, and
    # this module must stay importable without it.
    if ndc_status is not None:
        label = str(getattr(ndc_status, "status", "")).strip().upper()
        span = " ".join(
            x for x in (getattr(ndc_status, "start_date", ""), getattr(ndc_status, "end_date", ""))
            if x
        )
        if label == "OBSOLETE":
            add(
                "NDC_OBSOLETE",
                f"RxNorm lists this NDC obsolete ({span or 'no dates given'})",
                RXNORM,
                _RXNAV_URL,
            )
        elif label == "ACTIVE" and not in_directory:
            add(
                "NDC_ACTIVE_UNLISTED",
                f"Not in the FDA NDC Directory; RxNorm lists it active ({span or 'no dates given'})",
                RXNORM,
                _RXNAV_URL,
            )
    elif not in_directory:
        add("NDC_UNRESOLVED", "No formal source recognised this NDC", RXNORM, _RXNAV_URL)

    # --- data quality ------------------------------------------------------
    if in_directory and marketing_end_date is None and listing_expiration_date is None:
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


@dataclass(frozen=True)
class CertSignal:
    status: str
    codes: list[str] = field(default_factory=list)


def signal_for_ndc(session: Session, ndc: str) -> CertSignal:
    """Fetch current compliance status and active finding codes for an NDC."""
    from sqlalchemy import select
    from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
    from .models import CertificationFinding, DrugCertification

    try:
        key = ndc11(str(ndc))
    except ValueError:
        key = str(ndc).strip()

    record = session.get(DrugCertification, key)
    if record is None and key != str(ndc).strip():
        record = session.get(DrugCertification, str(ndc).strip())
    if record is None:
        return CertSignal(status="unknown", codes=[])

    try:
        findings = session.scalars(
            select(CertificationFinding.code).where(
                CertificationFinding.ndc.in_([key, str(ndc).strip()])
            )
        ).all()
    except (ProgrammingError, SQLAlchemyError):
        findings = []

    return CertSignal(status=str(record.status), codes=sorted(set(findings)))


def recalls_for(session: Session, ndc: str) -> list[Recall]:
    """Active recall findings recorded for this NDC."""
    from sqlalchemy import select
    from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
    from .models import CertificationFinding

    try:
        key = ndc11(str(ndc))
    except ValueError:
        key = str(ndc).strip()

    try:
        rows = session.scalars(
            select(CertificationFinding).where(
                CertificationFinding.ndc.in_([key, str(ndc).strip()]),
                CertificationFinding.code.in_(["RECALL_CLASS_I", "RECALL_CLASS_II", "RECALL_CLASS_III"]),
            )
        ).all()
    except (ProgrammingError, SQLAlchemyError):
        return []

    class_map = {
        "RECALL_CLASS_I": "Class I",
        "RECALL_CLASS_II": "Class II",
        "RECALL_CLASS_III": "Class III",
    }
    return [
        Recall(
            classification=class_map.get(r.code, "Class II"),
            status="Ongoing",
            recall_number=r.source_ref or "",
            reason=r.message or "",
            raw=r.raw or {},
        )
        for r in rows
    ]


def shortages_for(session: Session, ndc: str) -> list[Shortage]:
    """Active FDA drug shortages recorded for this NDC."""
    from sqlalchemy import select
    from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
    from .models import CertificationFinding, ShortageEvent

    try:
        key = ndc11(str(ndc))
    except ValueError:
        key = str(ndc).strip()

    out: list[Shortage] = []
    try:
        events = session.scalars(
            select(ShortageEvent).where(ShortageEvent.ndc.in_([key, str(ndc).strip()]))
        ).all()
        for r in events:
            raw = r.raw or {}
            categories = raw.get("therapeutic_category") or []
            out.append(
                Shortage(
                    status=r.status or raw.get("status") or "Current",
                    generic_name=str(raw.get("generic_name") or ""),
                    therapeutic_category=", ".join(str(c) for c in categories)
                    if isinstance(categories, list)
                    else str(categories),
                    update_date=str(raw.get("update_date") or ""),
                    raw=raw,
                )
            )
    except (ProgrammingError, SQLAlchemyError):
        pass

    if not out:
        try:
            findings = session.scalars(
                select(CertificationFinding).where(
                    CertificationFinding.ndc.in_([key, str(ndc).strip()]),
                    CertificationFinding.code.in_(["SHORTAGE_CURRENT", "SHORTAGE_DISCONTINUING"]),
                )
            ).all()
            for f in findings:
                status = "To Be Discontinued" if f.code == "SHORTAGE_DISCONTINUING" else "Current"
                out.append(
                    Shortage(
                        status=status,
                        generic_name="",
                        therapeutic_category="",
                        update_date=f.source_ref or "",
                        raw=f.raw or {},
                    )
                )
        except (ProgrammingError, SQLAlchemyError):
            pass

    return out

