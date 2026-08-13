"""Shared metadata. Alembic autogenerate reads Base.metadata, so any table a
service owns must be imported here before a migration is generated."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
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
