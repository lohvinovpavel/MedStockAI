"""Wave 0: hospital_id Text → uuid FK on every tenant table.

Merges the two demo heads (forecast/stock_daily vs warning_letters) and
collapses DEMO GENERAL HOSPITAL (all-zeros UUID) into St Mary's General so
auth, seed_demo, seed_stock and seed_patients share one tenant.

Revision ID: 20260818_hospital_uuid
Revises: 20260818_stock_daily, 20260818_wl
Create Date: 2026-08-18
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import text

revision: str = "20260818_hospital_uuid"
down_revision: tuple[str, str] = ("20260818_stock_daily", "20260818_wl")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Copied from medstock_shared.demo_tenant so this revision stays valid if
# that module later moves. uuid5(NAMESPACE_URL, "https://medstock.ai/demo/st-marys-general").
CANONICAL_ID = uuid.UUID("3b699ddd-5c28-526e-a781-b66be293bec8")
LEGACY_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ST_MARYS = "St Mary's General"
LEGACY_NAME = "DEMO GENERAL HOSPITAL"

TENANT_TABLES = (
    "formulary_item",
    "stock_snapshot",
    "patient",
    "assessment_log",
    "consumption_daily",
    "forecast_point",
    "stock_daily",
)

# Unique key columns besides hospital_id, used when remapping would collide.
UNIQUE_TAIL: dict[str, tuple[str, ...]] = {
    "formulary_item": ("rxcui",),
    "stock_snapshot": ("ndc", "facility_id", "location_id"),
    "consumption_daily": ("facility_id", "ndc", "date"),
    "forecast_point": ("facility_id", "ndc", "run_id", "target_date"),
    "stock_daily": ("facility_id", "ndc", "date"),
}

def upgrade() -> None:
    for table in TENANT_TABLES:
        op.alter_column(
            table,
            "hospital_id",
            existing_type=sa.Text(),
            type_=UUID(as_uuid=True),
            postgresql_using="hospital_id::uuid",
            existing_nullable=False,
        )

    conn = op.get_bind()
    keep, drop_ids = _keep_and_drop(conn)
    if keep is not None:
        for drop in drop_ids:
            _remap_hospital(conn, drop, keep)
            conn.execute(text("DELETE FROM hospital WHERE id = :id"), {"id": drop})

    _delete_orphan_tenant_rows(conn)
    conn.execute(
        text(
            "DELETE FROM stock_snapshot WHERE location_id IN "
            "('main-pharmacy', 'icu', 'ward-3')"
        )
    )

    for table in TENANT_TABLES:
        op.create_foreign_key(
            f"fk_{table}_hospital",
            table,
            "hospital",
            ["hospital_id"],
            ["id"],
        )


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.drop_constraint(f"fk_{table}_hospital", table, type_="foreignkey")
        op.alter_column(
            table,
            "hospital_id",
            existing_type=UUID(as_uuid=True),
            type_=sa.Text(),
            postgresql_using="hospital_id::text",
            existing_nullable=False,
        )


def _keep_and_drop(conn) -> tuple[uuid.UUID | None, list[uuid.UUID]]:
    rows = list(conn.execute(text("SELECT id, name FROM hospital")))
    st_marys = next((hid for hid, name in rows if name == ST_MARYS), None)
    drop: list[uuid.UUID] = []
    for hid, name in rows:
        if (hid == LEGACY_ID or name == LEGACY_NAME) and hid != st_marys:
            drop.append(hid)

    if st_marys is not None and st_marys != LEGACY_ID:
        return st_marys, drop

    if not drop and st_marys is None:
        # Empty database: do not invent a tenant. Auth seed creates St Mary's.
        return None, []

    # All-zeros St Mary's, or DEMO GENERAL with no St Mary's: move onto the
    # canonical id so a rebuilt environment matches a newly seeded one.
    keep = CANONICAL_ID
    if st_marys == LEGACY_ID:
        drop = [LEGACY_ID, *[i for i in drop if i != LEGACY_ID]]
    conn.execute(
        text(
            "INSERT INTO hospital (id, name) VALUES (:id, :name) "
            "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name"
        ),
        {"id": keep, "name": ST_MARYS},
    )
    return keep, [i for i in drop if i != keep]


def _remap_hospital(conn, src: uuid.UUID, dst: uuid.UUID) -> None:
    src_facs = list(
        conn.execute(
            text("SELECT id, code FROM facility WHERE hospital_id = :src"),
            {"src": src},
        )
    )
    dst_by_code = {
        code: fid
        for fid, code in conn.execute(
            text("SELECT id, code FROM facility WHERE hospital_id = :dst"),
            {"dst": dst},
        )
    }
    for src_fid, code in src_facs:
        dst_fid = dst_by_code.get(code)
        if dst_fid is not None:
            _merge_facility(conn, src_fid, dst_fid)
        else:
            conn.execute(
                text("UPDATE facility SET hospital_id = :dst WHERE id = :id"),
                {"dst": dst, "id": src_fid},
            )

    for table, tail in UNIQUE_TAIL.items():
        _remap_unique(conn, table, src, dst, tail)
    for table in ("patient", "assessment_log"):
        conn.execute(
            text(f"UPDATE {table} SET hospital_id = :dst WHERE hospital_id = :src"),
            {"dst": dst, "src": src},
        )

    conn.execute(
        text(
            "DELETE FROM membership m WHERE hospital_id = :src AND EXISTS ("
            "  SELECT 1 FROM membership k WHERE k.user_id = m.user_id AND k.hospital_id = :dst"
            ")"
        ),
        {"src": src, "dst": dst},
    )
    conn.execute(
        text("UPDATE membership SET hospital_id = :dst WHERE hospital_id = :src"),
        {"dst": dst, "src": src},
    )


def _merge_facility(conn, src_fid: int, dst_fid: int) -> None:
    src_locs = list(
        conn.execute(
            text("SELECT id, code FROM storage_location WHERE facility_id = :id"),
            {"id": src_fid},
        )
    )
    dst_loc_by_code = {
        code: lid
        for lid, code in conn.execute(
            text("SELECT id, code FROM storage_location WHERE facility_id = :id"),
            {"id": dst_fid},
        )
    }
    for src_lid, code in src_locs:
        dst_lid = dst_loc_by_code.get(code)
        if dst_lid is not None:
            conn.execute(
                text(
                    "UPDATE location_condition AS s SET location_id = :dst "
                    "WHERE s.location_id = :src AND NOT EXISTS ("
                    "  SELECT 1 FROM location_condition d"
                    "  WHERE d.location_id = :dst AND d.ts = s.ts"
                    ")"
                ),
                {"src": src_lid, "dst": dst_lid},
            )
            conn.execute(
                text("DELETE FROM location_condition WHERE location_id = :src"),
                {"src": src_lid},
            )
            conn.execute(
                text("DELETE FROM storage_location WHERE id = :id"),
                {"id": src_lid},
            )
        else:
            conn.execute(
                text("UPDATE storage_location SET facility_id = :dst WHERE id = :id"),
                {"dst": dst_fid, "id": src_lid},
            )

    for table, extra in (
        ("stock_snapshot", "AND d.ndc = s.ndc AND d.location_id IS NOT DISTINCT FROM s.location_id"),
        ("consumption_daily", "AND d.ndc = s.ndc AND d.date = s.date"),
        ("forecast_point", "AND d.ndc = s.ndc AND d.run_id = s.run_id AND d.target_date = s.target_date"),
        ("stock_daily", "AND d.ndc = s.ndc AND d.date = s.date"),
    ):
        conn.execute(
            text(
                f"UPDATE {table} AS s SET facility_id = :dst "
                f"WHERE s.facility_id = :src AND NOT EXISTS ("
                f"  SELECT 1 FROM {table} d"
                f"  WHERE d.hospital_id = s.hospital_id AND d.facility_id = :dst {extra}"
                f")"
            ),
            {"src": src_fid, "dst": dst_fid},
        )
        conn.execute(
            text(f"DELETE FROM {table} WHERE facility_id = :src"),
            {"src": src_fid},
        )
    conn.execute(text("DELETE FROM facility WHERE id = :id"), {"id": src_fid})


def _remap_unique(conn, table: str, src: uuid.UUID, dst: uuid.UUID, tail: tuple[str, ...]) -> None:
    match = " AND ".join(f"d.{col} IS NOT DISTINCT FROM s.{col}" for col in tail)
    conn.execute(
        text(
            f"UPDATE {table} AS s SET hospital_id = :dst "
            f"WHERE s.hospital_id = :src AND NOT EXISTS ("
            f"  SELECT 1 FROM {table} d"
            f"  WHERE d.hospital_id = :dst AND {match}"
            f")"
        ),
        {"src": src, "dst": dst},
    )
    conn.execute(
        text(f"DELETE FROM {table} WHERE hospital_id = :src"),
        {"src": src},
    )


def _delete_orphan_tenant_rows(conn) -> None:
    for table in TENANT_TABLES:
        conn.execute(
            text(
                f"DELETE FROM {table} WHERE hospital_id NOT IN (SELECT id FROM hospital)"
            )
        )
