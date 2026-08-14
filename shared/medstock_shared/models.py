"""Shared metadata. Alembic autogenerate reads Base.metadata, so any table a
service owns must be imported here before a migration is generated."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AICache(Base):
    """No queue, no worker. `ask_ai()` in `ai.py` calls Gemini inline and
    keeps the answer here so the same question is never paid for twice.

    Deliberately not a tenant table — no `hospital_id`, no RLS. The payload
    behind `dedupe_key` is reference data (drug names, RxCUI, shortage
    text), never PHI, so two hospitals asking the identical question share
    the identical cached answer. That is a feature: it is what makes the
    cache work across the whole system, not just within one hospital.
    """

    __tablename__ = "ai_cache"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("type", "dedupe_key", name="uq_ai_cache_type_dedupe"),)


# --- Reference tables (services/services.md §1.1): global, no RLS, written
# only by services/ingest. Each keeps the source's raw JSON in `raw` and a
# natural key to upsert on — structured columns get added as the exposure
# query in `inventory` actually needs them, not guessed ahead of that.
# ponytail: minimal shape until a real feed response is wired up; column
# names may shift once services/ingest/README.md's TODOs are resolved.


class Drug(Base):
    __tablename__ = "drug"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ndc: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ShortageEvent(Base):
    __tablename__ = "shortage_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    ndc: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DrugPrice(Base):
    __tablename__ = "drug_price"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ndc: Mapped[str] = mapped_column(Text, nullable=False)
    effective_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unit_price: Mapped[str | None] = mapped_column(Text)  # NADAC $/unit, kept as-received
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (UniqueConstraint("ndc", "effective_date", name="uq_drug_price_ndc_date"),)


class RxnormEdge(Base):
    __tablename__ = "rxnorm_edge"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rxcui_from: Mapped[str] = mapped_column(Text, nullable=False)
    rxcui_to: Mapped[str] = mapped_column(Text, nullable=False)
    relationship: Mapped[str] = mapped_column(Text, nullable=False)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("rxcui_from", "rxcui_to", "relationship", name="uq_rxnorm_edge"),
    )


# --- Identity tables (docs/auth-spec.md §1): owned by `auth`, and the one
# documented exception to the "always go through session_scope" rule. Login
# runs *before* there is a hospital_id to set, so these three carry no RLS
# policies and are queried through SessionLocal directly.


class Hospital(Base):
    __tablename__ = "hospital"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AppUser(Base):
    """Not `user` — reserved word in Postgres."""

    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # CITEXT so Ann@x.org and ann@x.org cannot become two accounts. The
    # migration creates the extension before this table.
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Membership(Base):
    """Role belongs to the membership, not the user — "director at A,
    pharmacist at B" is the case that would otherwise force an auth rewrite.

    `uq_membership_one_hospital_per_user` is the "one hospital per user"
    decision (docs/services.md §8 #4). Dropping that one constraint plus
    adding a hospital picker at login is the whole multi-hospital change.
    """

    __tablename__ = "membership"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True
    )
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospital.id"), primary_key=True
    )
    # Must stay in sync with the keys of PERMS in auth.py.
    role: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "role IN ('pharmacist','physician','director','admin')", name="ck_membership_role"
        ),
        UniqueConstraint("user_id", name="uq_membership_one_hospital_per_user"),
    )


# --- Tenant tables (UC-1): owned by `inventory` (writes) and read by
# `analogue` to boost search hits. No application `WHERE hospital_id` — RLS
# + `session_scope` are meant to be the tenant filter once policies exist
# (services.md §8 #2); not yet enforced, so this is the shape they will
# filter, not a working guarantee today.
#
# hospital_id here is Text, not a FK to hospital.id (UUID) above — the two
# were modeled independently by different owners in parallel. Flag for
# whoever owns inventory: worth a follow-up migration once both tables have
# real rows, not something to silently retype in a merge conflict resolution.


class FormularyItem(Base):
    """Tenant formulary. Analogue reads `rxcui` to boost UC-1 search hits.
    Inventory will own writes (`POST /formulary/import`). No application
    `WHERE hospital_id` — RLS + `session_scope` are the tenant filter.
    """

    __tablename__ = "formulary_item"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hospital_id: Mapped[str] = mapped_column(Text, nullable=False)
    rxcui: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("hospital_id", "rxcui", name="uq_formulary_hospital_rxcui"),)


class StockSnapshot(Base):
    """On-hand quantity per hospital / NDC / location. Empty string location
    is the hospital-wide bucket until warehouse locations exist.
    """

    __tablename__ = "stock_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hospital_id: Mapped[str] = mapped_column(Text, nullable=False)
    ndc: Mapped[str] = mapped_column(Text, nullable=False)
    location_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("hospital_id", "ndc", "location_id", name="uq_stock_hospital_ndc_loc"),
    )


# --- Certification (docs/compliance-usecases.md COMP-1). Reference class per
# services.md §1.1: FDA certification is identical for every hospital, so it is
# polled once for all of them — no hospital_id, no RLS. Written by
# services/ingest/app/certification.py, read by `compliance`.


class DrugCertification(Base):
    """The traffic light for one NDC.

    `status` is **derived**, never authored: `compliance.app.rules.status_for()`
    computes it from the findings below. It is stored so `GET /status` is one
    indexed read instead of a re-evaluation per request. `ruleset_version`
    records which rules produced it, so a stored colour can always explain
    itself even after the thresholds change.
    """

    __tablename__ = "drug_certification"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ndc: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # green|yellow|red|unknown
    marketing_end_date: Mapped[datetime | None] = mapped_column(Date)
    listing_expiration_date: Mapped[datetime | None] = mapped_column(Date)
    marketing_category: Mapped[str | None] = mapped_column(Text)
    application_number: Mapped[str | None] = mapped_column(Text)
    labeler: Mapped[str | None] = mapped_column(Text)
    # scheduled = a CronJob wrote it; on_demand = COMP-2 explored it. A Director
    # export that cannot say where a colour came from is not evidence.
    provenance: Mapped[str] = mapped_column(Text, nullable=False, server_default="scheduled")
    ruleset_version: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    # Only on_demand rows carry a TTL — nothing refreshes them on a schedule.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class CertificationFinding(Base):
    """One reason behind a colour, with the source that produced it.

    The colour is re-derivable from these rows: change a threshold in
    `rules.py` and the findings are replayed, not re-fetched from FDA.
    """

    __tablename__ = "certification_finding"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ndc: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    code: Mapped[str] = mapped_column(Text, nullable=False)  # LISTING_EXPIRED, RECALL_CLASS_I, …
    severity: Mapped[str] = mapped_column(Text, nullable=False)  # red|yellow|info
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # "openFDA NDC Directory"
    source_url: Mapped[str | None] = mapped_column(Text)
    # Distinguishes two recalls of the same class on the same drug, and is what
    # makes re-running the CronJob an upsert rather than a duplicate.
    source_ref: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")

    __table_args__ = (
        UniqueConstraint("ndc", "code", "source_ref", name="uq_cert_finding_natural"),
    )
