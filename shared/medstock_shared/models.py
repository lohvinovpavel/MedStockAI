"""Shared metadata. Alembic autogenerate reads Base.metadata, so any table a
service owns must be imported here before a migration is generated."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB, UUID
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
    # Storage requirements are class-level (refrigerated 2–8°C, controlled room
    # temp 15–25°C, freezer −25…−15°C), not parsed from SPL label text — the
    # warehouse excursion check compares location telemetry against these. A
    # future ingest job may overwrite them with label-derived values per NDC.
    storage_class: Mapped[str | None] = mapped_column(Text)  # refrigerated|crt|freezer
    storage_min_c: Mapped[float | None] = mapped_column(Numeric(5, 2))
    storage_max_c: Mapped[float | None] = mapped_column(Numeric(5, 2))
    humidity_max_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
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
    # B1: facility_id carries site identity; location_id survives as the
    # intra-facility shelf code (matches storage_location.code). Nullable
    # because pre-B1 rows exist; the demo seeder backfills it.
    facility_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("facility.id"))
    location_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "hospital_id",
            "ndc",
            "facility_id",
            "location_id",
            name="uq_stock_hospital_ndc_fac_loc",
            postgresql_nulls_not_distinct=True,
        ),
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
    marketing_end_date: Mapped[date | None] = mapped_column(Date)
    listing_expiration_date: Mapped[date | None] = mapped_column(Date)
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


class DrugRiskProfile(Base):
    """PP-3: which patient characteristics raise the risk of which reaction.

    Reference class — the profile describes a *drug*, not a person, so there is
    no `hospital_id` and no RLS. That is also why the extraction can use a model
    at all: the input is a public label and no patient is ever involved
    (docs/prognosis-and-procurement.md §0).

    `status` gates everything. A profile is written `awaiting_approval` and can
    colour nothing until a pharmacist accepts it — a model's reading of a label
    is a proposal, not a clinical fact.
    """

    __tablename__ = "drug_risk_profile"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rxcui: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    reaction: Mapped[str] = mapped_column(Text, nullable=False)
    seriousness: Mapped[str] = mapped_column(Text, nullable=False, server_default="moderate")
    # [{"feature": "egfr_band", "op": "at_or_below", "value": "45-59"}, …]
    # Validated against a closed vocabulary before it is written; see
    # medstock_shared.ai_tasks._prognosis_is_applicable.
    risk_factors: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    # Verbatim from the label section named below — the reviewable basis the
    # FDA CDS exemption turns on.
    citation: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    section: Mapped[str | None] = mapped_column(Text)
    spl_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="awaiting_approval")
    approved_by: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("rxcui", "reaction", name="uq_drug_risk_profile_natural"),
    )


class PrognosisAssumption(Base):
    """PP-4: a number the forecast assumes rather than measures.

    Reference class, no `hospital_id` — these are model parameters, not tenant
    data.

    `switch_rate` lives here rather than in code for one reason: it is the share
    of flagged patients a pharmacist actually switches, and nobody has measured
    it. A literal in a function reads like a derived constant. A row with a
    `note` reads like what it is — an assumption someone chose, which a director
    can see, question and change without a deploy
    (docs/prognosis-and-procurement.md §2.2).
    """

    __tablename__ = "prognosis_assumption"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    # Why this number and not another. Shown wherever the forecast is.
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


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


# --- Demo patient registry (physician prescription cart). Tenant table with
# deliberate PHI for the capstone demo — not production BAA posture. Rules
# engine still receives only a PatientVector via patient_row_to_vector().


class Patient(Base):
    """Hospital-scoped patient profile for the physician prescribe demo.

    `allergy_codes` / `condition_codes` feed the de-identified vector. Name and
    date of birth stay here and never enter ask_ai() / ai_cache.
    """

    __tablename__ = "patient"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    blood_group: Mapped[str | None] = mapped_column(Text)
    allergy_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    condition_codes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


# --- Warehouse (docs/backend/specs/B1-facility-registry.md, issue #8): tenant
# facility/location registry plus the two generated time series the demo runs
# on — daily drug consumption (what prediction forecasts from, E1) and hourly
# storage-condition telemetry (what the excursion check reads). Written by
# services/ingest seed_demo; served by services/warehouse.


class Facility(Base):
    """One physical site of a hospital (campus, clinic, off-site warehouse).

    `code` is the stable slug the web client sends (`central`, `riverside`, …);
    `id` is internal. `operated = false` marks partner sites shown in the
    shortage matrix but never valid as an order/transfer target. Distance is
    computed from lat/lon per request, never stored (B1 rule 3).
    """

    __tablename__ = "facility"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospital.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    lat: Mapped[float | None] = mapped_column(Numeric(9, 6))
    lon: Mapped[float | None] = mapped_column(Numeric(9, 6))
    operated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('Hospital','Clinic','Pharmacy','Warehouse')", name="ck_facility_type"
        ),
        UniqueConstraint("hospital_id", "code", name="uq_facility_hospital_code"),
    )


class StorageLocation(Base):
    """Storage location inside a facility (ward fridge, shelf, cold room).

    Flat list keyed by facility (B1: "the tree can wait"). `kind` drives
    condition simulation and which storage classes belong here; `code` is what
    `stock_snapshot.location_id` carries as the intra-facility shelf id.
    """

    __tablename__ = "storage_location"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    facility_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("facility.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('room','fridge','freezer','cold_room')", name="ck_storage_location_kind"
        ),
        UniqueConstraint("facility_id", "code", name="uq_storage_location_facility_code"),
    )


class ConsumptionDaily(Base):
    """Daily drug consumption per facility — the history prediction (E1)
    forecasts from and the warehouse consumption chart plots.

    Pre-aggregated on purpose: when B4's consume-event ledger arrives it
    becomes the source and this table the derived rollup, unchanged. Both ids
    carried — consumption is physically NDC-grain, forecasts are requested by
    RxCUI (E1 `GET /forecast/{rxcui}`). `stockout = true` marks recorded zeros
    that are censoring (shelf was empty), not absence of demand.
    """

    __tablename__ = "consumption_daily"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hospital_id: Mapped[str] = mapped_column(Text, nullable=False)
    facility_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("facility.id"), nullable=False)
    ndc: Mapped[str] = mapped_column(Text, nullable=False)
    rxcui: Mapped[str] = mapped_column(Text, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    qty_consumed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stockout: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    __table_args__ = (
        UniqueConstraint(
            "hospital_id", "facility_id", "ndc", "date", name="uq_consumption_daily_natural"
        ),
        Index("ix_consumption_daily_series", "facility_id", "ndc", "date"),
    )


class LocationCondition(Base):
    """Hourly temperature/humidity reading for one storage location. Tenancy
    rides on the location → facility → hospital chain; no direct hospital_id.
    """

    __tablename__ = "location_condition"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    location_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("storage_location.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    temperature_c: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    humidity_pct: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)

    __table_args__ = (
        UniqueConstraint("location_id", "ts", name="uq_location_condition_natural"),
    )
