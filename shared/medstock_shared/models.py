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
    """No queue, no worker. `ask_ai()` in `ai/core.py` calls Gemini inline and
    keeps the answer here so the same question is never paid for twice.

    Deliberately not a tenant table — no `hospital_id`, no RLS. The payload
    behind `dedupe_key` is reference data (drug names, RxCUI, shortage
    text), never PHI, so two hospitals asking the identical question share
    the identical cached answer. That is a feature: it is what makes the
    cache work across the whole system, not just within one hospital.

    Provenance (which user asked, from which hospital) deliberately does not
    live here either — adding it would either break that cross-hospital
    sharing or return one user's row to another. It belongs on an audit row
    that references this one, not on this row itself (docs/ai-module-plan.md
    §0.3) — see `AIAuditLog` below.

    `prompt_version` is part of the unique key, not just a label: it is what
    makes editing a prompt in ai_tasks.py invalidate its own cache instead of
    silently keeps serving answers the old prompt produced.
    """

    __tablename__ = "ai_cache"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="v1")
    model_name: Mapped[str] = mapped_column(Text, nullable=False, server_default="unknown")
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "type", "prompt_version", "dedupe_key", name="uq_ai_cache_type_promptver_dedupe"
        ),
    )


class AIAuditLog(Base):
    """Who asked the model what, and what came back. Sibling of
    `AssessmentLog` below, same actor/request_id shape, for the same reason:
    the audit read is "what happened, newest first" either way.

    `hospital_id` is nullable UUID, unlike `AssessmentLog`'s — most `ask_ai()`
    callers are a pharmacist through `analogue`, tenant-scoped like anything
    else in §1.2, but `ingest`'s offline CronJobs (`prognosis`) process a
    public FDA label with no hospital attached to the call at all, the same
    reason `ai_cache` above has no tenant column. `actor_id` is never null:
    ingest's calls are still attributable, to `'system:ingest'`.

    Append-only: wave 2 REVOKEs UPDATE/DELETE from app_role. FORCE RLS is
    not applied here because `write_audit()` uses SessionLocal without
    `session_scope`, so it never sets `app.hospital_id`; a tenant policy
    would fail-open and silently drop provenance rows.
    Written from Python, not a trigger — the event being audited is an
    outbound API call, not a row mutation, so there is no row for a trigger
    to hang off.
    """

    __tablename__ = "ai_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospital.id"), nullable=True
    )
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    task_type: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    # cache_hit | live | breaker_open | error -- see docs/ai-module-plan.md §5.
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    # ponytail: unused until the Phase 4 copilot exists; a JSONB column with a
    # default costs nothing today and saves a second migration on this table
    # when it does. [] until then, always.
    tools_called: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_ai_audit_hospital_time", "hospital_id", "created_at"),
        Index("ix_ai_audit_dedupe", "dedupe_key"),
    )


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
# hospital_id is uuid FK to hospital.id on every tenant table (wave 0).
# Wave 2 (A4) ENABLE/FORCE RLS on tenant tables; session_scope SETs ROLE
# app_role so a superuser connection cannot bypass FORCE.


class FormularyItem(Base):
    """Tenant formulary. Analogue reads `rxcui` to boost UC-1 search hits.
    Inventory will own writes (`POST /formulary/import`). No application
    `WHERE hospital_id` — RLS + `session_scope` are the tenant filter.
    """

    __tablename__ = "formulary_item"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospital.id"), nullable=False
    )
    rxcui: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("hospital_id", "rxcui", name="uq_formulary_hospital_rxcui"),)


class StockSnapshot(Base):
    """On-hand quantity per hospital / NDC / location.

    `quantity` is a derived rollup of `stock_batch` (B4 trigger). Empty
    string location is the intra-facility shelf code (matches
    `storage_location.code`).
    """

    __tablename__ = "stock_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospital.id"), nullable=False
    )
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


class StockBatch(Base):
    """One received lot. `stock_snapshot.quantity` is the rollup of these
    rows (B4 trigger), never authored by the receive endpoint itself.
    """

    __tablename__ = "stock_batch"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospital.id"), nullable=False
    )
    facility_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("facility.id"), nullable=False)
    ndc: Mapped[str] = mapped_column(Text, nullable=False)
    lot: Mapped[str] = mapped_column(Text, nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    location_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_stock_batch_qty"),
        UniqueConstraint(
            "hospital_id", "facility_id", "ndc", "lot", name="uq_stock_batch_natural"
        ),
        Index("ix_stock_batch_fefo", "hospital_id", "ndc", "expiry_date"),
    )


class ParLevel(Base):
    """Reorder point and target per facility + NDC (B5). Status on B2 is
    derived from this, never stored.
    """

    __tablename__ = "par_level"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospital.id"), nullable=False
    )
    facility_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("facility.id"), nullable=False)
    ndc: Mapped[str] = mapped_column(Text, nullable=False)
    reorder_point: Mapped[int] = mapped_column(Integer, nullable=False)
    target_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("reorder_point >= 0", name="ck_par_reorder_nonneg"),
        CheckConstraint("target_qty > reorder_point", name="ck_par_target_above_reorder"),
        UniqueConstraint("hospital_id", "facility_id", "ndc", name="uq_par_level_natural"),
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
    # No `onupdate`: every writer of this table upserts, and onupdate does not
    # fire on INSERT .. ON CONFLICT DO UPDATE -- Core sees an insert and the
    # conflict branch is the database's business. Leaving it on reads as a
    # guarantee that this stamp maintains itself, and it does not, so the
    # writer sets it explicitly. Same trap as DrugRiskProfile.extracted_at.
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
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
    # Who last ruled on it, either way. A rejection has a reviewer too, and only
    # `status` says which way they ruled.
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    # Not derivable from extracted_at, which moves on re-extraction.
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why. Chiefly why *not* — a rejected extraction that records its reason can
    # be re-reviewed against that reason instead of from scratch.
    review_note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # No `onupdate`. This is the extraction date gate 4 versions a prediction by
    # (docs/prognosis-and-procurement.md §1.3) — approving a profile is not
    # re-extracting it, and an ORM update carrying onupdate would silently
    # restamp the row every time a pharmacist ruled on it. Re-extraction sets it
    # explicitly instead; see services/ingest/app/prognosis.py.
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("rxcui", "reaction", name="uq_drug_risk_profile_natural"),
        CheckConstraint(
            "status IN ('awaiting_approval', 'approved', 'rejected')",
            name="ck_drug_risk_profile_status",
        ),
        # Declared here because 20260817_profile_review created it. The review
        # queue reads WHERE status = .. ORDER BY extracted_at, which is what the
        # composite serves; leaving it out of the metadata does not remove the
        # index, it just hides it from alembic.
        Index("ix_drug_risk_profile_status", "status", "extracted_at"),
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
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospital.id"), nullable=False, index=True
    )
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    blood_group: Mapped[str | None] = mapped_column(Text)
    allergy_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    condition_codes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    # Tier 3 input, as "GENE:phenotype" in CPIC's vocabulary. Reported by the
    # lab, never derived here — see PatientVector.pgx_phenotypes.
    pgx_phenotypes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class WarningLetter(Base):
    """An FDA warning letter naming a firm — an enforcement action, not a defect.

    Reference class. docs/compliance-usecases.md §4.1 lists this as "open
    enforcement action against a labeler".

    **This table cannot tell you whether the action is still open**, and that is
    a property of the source rather than a shortcut taken here. FDA's export
    carries a `Closeout Letter` column and it is empty on every one of the
    1 000 rows it returns, while `Response Letter` is populated on 128 of them —
    so the closeout hyperlink simply does not survive the export. Any finding
    built on this therefore says a letter *was issued* and says plainly that
    closeout status is not published, rather than claiming an investigation is
    open when the data cannot support it.
    """

    __tablename__ = "warning_letter"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    # Normalised for the labeler match — see certification.firm_key.
    firm_key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    issue_date: Mapped[date | None] = mapped_column(Date)
    posted_date: Mapped[date | None] = mapped_column(Date)
    # "Center for Drug Evaluation and Research | CDER" and friends. Kept because
    # a tobacco letter and a drug letter are very different conversations.
    issuing_office: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    # Whether the firm has responded. Present in the export, unlike closeout.
    has_response: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    source_url: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("company_name", "issue_date", "subject", name="uq_warning_letter"),
    )


class ImportAlert(Base):
    """A foreign establishment on an FDA Import Alert Red List.

    Reference class. docs/compliance-usecases.md §4.1 — the import-certification
    source, and the one no JSON API exposes. Scraped weekly from
    accessdata.fda.gov by `services/ingest/app/import_alerts.py`.

    "Red List" is FDA's term and means detention without physical examination:
    goods from this establishment are held at the border unless the firm shows
    the violation is corrected. It is a standing regulatory posture, not an
    event, so the finding it produces is persistent rather than transient.
    """

    __tablename__ = "import_alert"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # "66-40" (GMP failure) or "66-41" (unapproved drugs). Indexed in
    # __table_args__ rather than with index=True, because the migration named it
    # `ix_import_alert_number` and index=True autogenerates
    # `ix_import_alert_alert_number` — a name-only disagreement that makes
    # `alembic check` fail and, worse, makes the next --autogenerate emit a drop
    # and recreate of a live index.
    alert_number: Mapped[str] = mapped_column(Text, nullable=False)
    firm_name: Mapped[str] = mapped_column(Text, nullable=False)
    # Normalised for matching — see medstock_shared.certification.firm_key.
    # Stored rather than computed on read so the match is indexable and so the
    # normalisation that produced it is inspectable.
    firm_key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    country: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    listed_at: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("alert_number", "firm_name", name="uq_import_alert_natural"),
        # Name taken from 20260817_import_news, not from index=True — see the
        # note on alert_number above. (`firm_key` keeps index=True because its
        # autogenerated name already matches what the migration created.)
        Index("ix_import_alert_number", "alert_number"),
    )


class NewsSignal(Base):
    """An informal report about a drug. **Can only ever raise yellow.**

    docs/compliance-usecases.md §4.3, and the rule there is structural rather
    than a preference: a news article is an unverified claim about a third
    party, so acting on it as fact would let the system tell a pharmacist a drug
    is uncertified because a blog said so. Only a government source sets red.
    Yellow means "check this", which is exactly what an unconfirmed report
    warrants.

    Reference class — an article is about a drug, not a hospital.
    """

    __tablename__ = "news_signal"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ndc: Mapped[str | None] = mapped_column(Text, index=True)
    # What the article was found by, kept so a reader can judge the match.
    query_term: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    domain: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AdrSignal(Base):
    """Tier 1: how far above baseline a reaction is *reported* for a drug.

    Reference class. Computed offline from openFDA FAERS by
    `services/ingest/app/faers.py` and read at stage 7.

    **These are reporting ratios, not risks.** FAERS is a spontaneous reporting
    system: it has no denominator, it is subject to notoriety bias (a drug in
    the news gets reported more), and it is confounded by indication (the
    reaction may belong to the disease, not the drug). A PRR of 4 means this
    reaction is reported four times more often for this drug than across all
    drugs — it does not mean the drug caused anything. Every message this table
    produces says so, because a ratio presented as a risk is the single way this
    tier misleads.

    `n_reports` is kept because the ratio alone is meaningless: 2 reports out of
    3 is a PRR that will move wildly on the next report, and the standard signal
    criteria require a minimum count for exactly that reason.
    """

    __tablename__ = "adr_signal"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rxcui: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # MedDRA preferred term as openFDA reports it, e.g. "LACTIC ACIDOSIS".
    reaction: Mapped[str] = mapped_column(Text, nullable=False)
    # Proportional reporting ratio and reporting odds ratio.
    prr: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    ror: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    # a — reports naming both this drug and this reaction.
    n_reports: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # a+b — all reports naming this drug, so the ratio can be re-derived.
    n_drug_reports: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # No `onupdate`: every writer of this table upserts, and onupdate does not
    # fire on INSERT .. ON CONFLICT DO UPDATE -- Core sees an insert and the
    # conflict branch is the database's business. Leaving it on reads as a
    # guarantee that this stamp maintains itself, and it does not, so the
    # writer sets it explicitly. Same trap as DrugRiskProfile.extracted_at.
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("rxcui", "reaction", name="uq_adr_signal_natural"),)


class PgxGuideline(Base):
    """Tier 3: a CPIC gene–drug recommendation, keyed the way CPIC keys it.

    Reference class — a guideline is about a drug and a phenotype, never about a
    person, so no `hospital_id`.

    `phenotype` holds CPIC's own `lookupkey` value ("Poor Metabolizer",
    "*57:01 positive"), not a vocabulary of ours. Inventing a parallel one would
    mean a mapping layer nobody could audit against the source, and the source
    is the whole point of a guideline lookup.

    `action_required` separates "this genotype changes prescribing" from "use
    per standard dosing". CPIC publishes no usable flag for it — its three
    candidate booleans are `false` on every row — so it is derived from the
    phenotype by `patient.is_baseline_phenotype`, which is the one piece of
    judgement in this feed and is documented as ours there.
    """

    __tablename__ = "pgx_guideline"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    gene: Mapped[str] = mapped_column(Text, nullable=False)
    rxcui: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    phenotype: Mapped[str] = mapped_column(Text, nullable=False)
    # Verbatim CPIC. Shown to the pharmacist as-is — the reviewable basis again.
    recommendation: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    implication: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # CPIC's own strength field: Strong | Moderate | Optional | No Recommendation.
    classification: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # CPIC level of the underlying gene-drug pair: A | B.
    evidence_level: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    action_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    population: Mapped[str] = mapped_column(Text, nullable=False, server_default="general")
    source_url: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("gene", "rxcui", "phenotype", "population", name="uq_pgx_guideline"),
    )


class AssessmentLog(Base):
    """Who asked what of the rules engine, and what it answered.

    Tenant class, and the only patient-adjacent table that is *supposed* to
    exist under the no-PHI design (docs/patient-profiling-usecases.md §7). It
    holds **no patient identifier at all** — `feature_hash` proves what was
    asked without recording who it was about, which is what makes the decision
    trail in docs/services.md §1.3 work while §2.4's "the audit log records the
    decision, not the patient" stays true. Asked "which patient was this?", this
    table cannot answer, and the hospital's own EHR can.

    `patient_ref` is deliberately excluded from the hash. It is opaque to us,
    but it is stable per patient, so hashing it would let anyone with the table
    group every assessment ever made about one person — a re-identification
    handle built out of the audit trail itself.

    Recovering a vector from `feature_hash` is possible in principle: the field
    space is small enough to enumerate. That is acceptable precisely because
    what it would recover is a de-identified band vector — age band, eGFR band,
    codes — and never an identity. The hash is an integrity check on the
    question, not a secret.

    `ruleset_version` rather than the `model_version` of §7's sketch: this
    pipeline is deterministic, so what has to be pinned to explain an old answer
    is the weight table and bands that produced it. When a Tier 2 model lands it
    gets its own column and its own table; a version string that silently means
    two different things would be worse than either.
    """

    __tablename__ = "assessment_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospital.id"), nullable=False
    )
    # One id per API call, echoed back to the caller so a clinician can quote it
    # when they disagree with an answer.
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    # The clinician, from the JWT `sub`. The decision is attributable to a
    # person even though the subject of it is not.
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    feature_hash: Mapped[str] = mapped_column(Text, nullable=False)
    ruleset_version: Mapped[str] = mapped_column(Text, nullable=False)
    # [{"rxcui": …, "verdict": …, "score": …, "codes": [...]}, …]
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # The audit read is "what happened at this hospital, newest first".
        Index("ix_assessment_log_hospital_time", "hospital_id", "created_at"),
        Index("ix_assessment_log_request", "request_id"),
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
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospital.id"), nullable=False
    )
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


class ForecastPoint(Base):
    """One forecast quantile row per (facility, ndc, target_date) within a run
    (spec E1). Runs are immutable across days and kept 90 days; a same-day
    re-run replaces that day's run in one transaction rather than accumulating.

    `data_through` is the last consumption date the run saw — constant within
    a run. Clients compare it against the newest consumption data to decide
    that a forecast has been outrun and a re-run is due.
    """

    __tablename__ = "forecast_point"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospital.id"), nullable=False
    )
    facility_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("facility.id"), nullable=False)
    ndc: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    data_through: Mapped[date] = mapped_column(Date, nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    p10: Mapped[float] = mapped_column(Numeric, nullable=False)
    p50: Mapped[float] = mapped_column(Numeric, nullable=False)
    p90: Mapped[float] = mapped_column(Numeric, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("p10 <= p50 AND p50 <= p90", name="ck_forecast_point_quantiles"),
        UniqueConstraint(
            "hospital_id",
            "facility_id",
            "ndc",
            "run_id",
            "target_date",
            name="uq_forecast_point_natural",
        ),
        Index("ix_forecast_lookup", "hospital_id", "facility_id", "ndc", "run_id"),
    )


class StockDaily(Base):
    """End-of-day on-hand per facility/NDC — the stock history the forecasts
    page draws left of "today". Mirrors consumption_daily's shape. No writer
    exists in production yet (B4 receiving events are the future source);
    the demo seeder plants a series consistent with consumption_daily that
    ends exactly at stock_snapshot's current quantity.
    """

    __tablename__ = "stock_daily"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospital.id"), nullable=False
    )
    facility_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("facility.id"), nullable=False)
    ndc: Mapped[str] = mapped_column(Text, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    qty_on_hand: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "hospital_id", "facility_id", "ndc", "date", name="uq_stock_daily_natural"
        ),
        Index("ix_stock_daily_series", "facility_id", "ndc", "date"),
    )


# --- Audit (docs/backend/specs/H1-append-only-audit-log.md): review_decision
# is the F1 shape; the append-only log is written by a trigger, never by
# application code. Wave 1 attaches the trigger only to review_decision —
# formulary_item / drug_certification writers still use SessionLocal without
# an actor, and those tables would fail the CHECK if the trigger fired.


class ReviewDecision(Base):
    """Human accept/reject of a restock recommendation or analogue switch.

    F1 writers are still open; the table exists so H1 has something to
    audit. `payload` is the recommendation exactly as shown, not a live join.
    """

    __tablename__ = "review_decision"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospital.id"), nullable=False
    )
    facility_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("facility.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_ref: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('restock_recommendation','analogue_substitution')",
            name="ck_review_decision_entity_type",
        ),
        CheckConstraint(
            "decision IN ('pending','approved','rejected')",
            name="ck_review_decision_decision",
        ),
    )


class AuditLogEntry(Base):
    """Append-only trail. Inserts come from `write_audit_entry()`; the app
    role cannot UPDATE or DELETE. At least one of actor_id / actor_system
    must be set — an unattributable change must not commit.
    """

    __tablename__ = "audit_log_entry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hospital.id"), nullable=False
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_system: Mapped[str | None] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    ai_dedupe_key: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "actor_id IS NOT NULL OR actor_system IS NOT NULL",
            name="ck_audit_log_entry_actor",
        ),
        Index("ix_audit_entity", "hospital_id", "entity_type", "entity_id", "occurred_at"),
        Index("ix_audit_time", "hospital_id", "occurred_at"),
    )
