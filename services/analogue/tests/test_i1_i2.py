"""I1/I2 copilot gateway: persistence, isolation, physician draft_order 403."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from app.main import app
from fastapi.testclient import TestClient
from medstock_shared.ai.tools import ToolDenied, declarations_for, execute
from medstock_shared.auth import Principal, current_principal
from medstock_shared.db import engine, session_scope
from medstock_shared.models import (
    CopilotConversation,
    CopilotMessage,
    Facility,
    Hospital,
    ParLevel,
    ReviewDecision,
    StockSnapshot,
    Supplier,
    SupplierCatalog,
)
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

HOSPITAL_A = uuid.UUID("00000000-0000-0000-0000-00000000c2a1")
HOSPITAL_B = uuid.UUID("00000000-0000-0000-0000-00000000c2b2")
ACTOR = uuid.UUID("00000000-0000-0000-0000-00000000c2c3")
OTHER = uuid.UUID("00000000-0000-0000-0000-00000000c2c4")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(current_principal, None)


def _client(
    role: str = "pharmacist",
    user_id: uuid.UUID = ACTOR,
    hospital_id: uuid.UUID = HOSPITAL_A,
) -> TestClient:
    app.dependency_overrides[current_principal] = lambda: Principal(
        str(user_id), str(hospital_id), role
    )
    return TestClient(app)


def _wipe_par_supplier(hospital_id: uuid.UUID, actor_id: uuid.UUID) -> None:
    """par_level/stock_snapshot/supplier carry an audit trigger that reads the
    `app.hospital_id` session GUC -- only session_scope() sets that, so a bare
    Session(engine) delete 500s on the trigger's own NOT NULL constraint
    (matches test_wave5.py's _wipe, same tables)."""
    with session_scope(str(hospital_id), str(actor_id), "test_teardown") as s:
        s.execute(delete(StockSnapshot).where(StockSnapshot.hospital_id == hospital_id))
        s.execute(delete(ParLevel).where(ParLevel.hospital_id == hospital_id))
        sids = list(s.scalars(select(Supplier.id).where(Supplier.hospital_id == hospital_id)))
        if sids:
            s.execute(delete(SupplierCatalog).where(SupplierCatalog.supplier_id.in_(sids)))
        s.execute(delete(Supplier).where(Supplier.hospital_id == hospital_id))


def _cleanup() -> None:
    _wipe_par_supplier(HOSPITAL_A, ACTOR)
    _wipe_par_supplier(HOSPITAL_B, OTHER)
    with Session(engine) as s:
        convs = list(
            s.scalars(
                select(CopilotConversation.id).where(
                    CopilotConversation.hospital_id.in_([HOSPITAL_A, HOSPITAL_B])
                )
            )
        )
        if convs:
            s.execute(delete(CopilotMessage).where(CopilotMessage.conversation_id.in_(convs)))
        s.execute(
            delete(CopilotConversation).where(
                CopilotConversation.hospital_id.in_([HOSPITAL_A, HOSPITAL_B])
            )
        )
        s.execute(delete(ReviewDecision).where(ReviewDecision.hospital_id.in_([HOSPITAL_A, HOSPITAL_B])))
        s.execute(delete(Facility).where(Facility.hospital_id.in_([HOSPITAL_A, HOSPITAL_B])))
        s.commit()


@pytest.fixture
def seeded():
    _cleanup()
    with Session(engine) as s:
        s.merge(Hospital(id=HOSPITAL_A, name="I2 HOSPITAL A"))
        s.merge(Hospital(id=HOSPITAL_B, name="I2 HOSPITAL B"))
        s.flush()
        fa = Facility(
            hospital_id=HOSPITAL_A, code="i2-a", name="I2 A",
            type="Hospital", lat=50.45, lon=30.52, operated=True,
        )
        fb = Facility(
            hospital_id=HOSPITAL_B, code="i2-b", name="I2 B",
            type="Hospital", lat=50.45, lon=30.52, operated=True,
        )
        s.add_all([fa, fb])
        s.commit()
        ids = {"a": fa.id, "b": fb.id}
    yield ids
    _cleanup()


def test_physician_draft_order_is_tool_denied():
    names = {d["name"] for d in declarations_for(Principal(str(ACTOR), str(HOSPITAL_A), "physician"))}
    assert "draft_order" not in names
    with pytest.raises(ToolDenied, match="may not call"):
        asyncio.run(
            execute(
                "draft_order",
                {
                    "facility_id": 1,
                    "supplier_id": 1,
                    "ndc": "00338011220",
                    "quantity": 10,
                    "review_decision_id": 1,
                },
                Principal(str(ACTOR), str(HOSPITAL_A), "physician"),
            )
        )


def test_list_pending_restock_recommendations_scoped_and_filtered(seeded):
    """Only pending restock_recommendation rows, only this hospital's --
    excludes another hospital's pending row, a decided row, and a pending
    row of the table's other entity_type (analogue_substitution)."""
    with session_scope(str(HOSPITAL_A), str(ACTOR)) as s:
        s.add(ReviewDecision(
            hospital_id=HOSPITAL_A, facility_id=seeded["a"], entity_type="restock_recommendation",
            entity_ref="00000000001", decision="pending",
            payload={"ndc": "00000000001", "name": "Test Drug", "quantity": 50,
                     "supplier_id": 1, "supplier_name": "Acme"},
        ))
        s.add(ReviewDecision(
            hospital_id=HOSPITAL_A, facility_id=seeded["a"], entity_type="restock_recommendation",
            entity_ref="00000000002", decision="approved",
            payload={"ndc": "00000000002", "quantity": 5},
        ))
        s.add(ReviewDecision(
            hospital_id=HOSPITAL_A, facility_id=seeded["a"], entity_type="analogue_substitution",
            entity_ref="not-restock", decision="pending", payload={},
        ))
    with session_scope(str(HOSPITAL_B), str(OTHER)) as s:
        s.add(ReviewDecision(
            hospital_id=HOSPITAL_B, facility_id=seeded["b"], entity_type="restock_recommendation",
            entity_ref="cross-tenant", decision="pending",
            payload={"ndc": "cross-tenant", "quantity": 1},
        ))

    result = asyncio.run(execute(
        "list_pending_restock_recommendations", {}, Principal(str(ACTOR), str(HOSPITAL_A), "admin")
    ))
    assert result["pending_total"] == 1
    assert not result["truncated"]
    item = result["items"][0]
    assert item["ndc"] == "00000000001"
    assert item["quantity"] == 50
    assert item["supplier_name"] == "Acme"
    assert item["facility_id"] == seeded["a"]

    # facility_id filter narrows to a facility with nothing pending
    empty = asyncio.run(execute(
        "list_pending_restock_recommendations",
        {"facility_id": seeded["b"]},
        Principal(str(ACTOR), str(HOSPITAL_A), "admin"),
    ))
    assert empty["pending_total"] == 0


def test_recommend_restock_materialises_and_proposes(seeded):
    """The gap this closes: 'create an order' had no way to reach a
    review_decision_id when list_pending_restock_recommendations was empty.
    recommend_restock computes one from par/on-hand/catalog, writes it as a
    pending restock_recommendation, and hands back the same proposal shape
    propose_order does -- so the chat flow no longer dead-ends."""
    ndc = "00000000009"
    with session_scope(str(HOSPITAL_A), str(ACTOR)) as s:
        supplier = Supplier(
            hospital_id=HOSPITAL_A, name="Recommend Test Pharma", lead_time_days=4,
            reliability_pct=Decimal("95.00"), shipping_flat=Decimal("60.00"),
            currency="USD", active=True,
        )
        s.add(supplier)
        s.flush()
        s.add(SupplierCatalog(
            supplier_id=supplier.id, ndc=ndc, unit_cost=Decimal("11.4000"),
            pack_size=10, min_order_qty=10,
        ))
        s.add(ParLevel(
            hospital_id=HOSPITAL_A, facility_id=seeded["a"], ndc=ndc,
            reorder_point=20, target_qty=180,
        ))
        s.add(StockSnapshot(hospital_id=HOSPITAL_A, facility_id=seeded["a"], ndc=ndc, quantity=6))
        s.flush()
        supplier_id = supplier.id

    principal = Principal(str(ACTOR), str(HOSPITAL_A), "pharmacist")
    result = asyncio.run(execute(
        "recommend_restock", {"facility_id": seeded["a"], "ndc": ndc}, principal,
    ))
    assert result["ndc"] == ndc
    assert result["supplier_id"] == supplier_id
    assert result["quantity"] > 0
    assert result["blocked"] is False
    review_decision_id = result["review_decision_id"]
    assert review_decision_id

    with session_scope(str(HOSPITAL_A), str(ACTOR)) as s:
        row = s.get(ReviewDecision, review_decision_id)
        assert row.decision == "pending"
        assert row.entity_type == "restock_recommendation"
        assert row.payload["ndc"] == ndc

    # Now reachable exactly the way draft_order/propose_order already are.
    followup = asyncio.run(execute(
        "list_pending_restock_recommendations", {"facility_id": seeded["a"]}, principal,
    ))
    assert any(item["review_decision_id"] == review_decision_id for item in followup["items"])


def test_recommend_restock_no_par_returns_note_not_error(seeded):
    """No par level for this NDC -- compute_recommendations returns nothing.
    Must degrade to an informative note, not a crash or a fabricated card."""
    result = asyncio.run(execute(
        "recommend_restock",
        {"facility_id": seeded["a"], "ndc": "00000000099"},
        Principal(str(ACTOR), str(HOSPITAL_A), "pharmacist"),
    ))
    assert "note" in result
    assert "review_decision_id" not in result


def test_conversation_crud_isolation_and_soft_delete(seeded):
    created = _client().post("/api/copilot/conversations", json={"facility_id": seeded["a"]})
    assert created.status_code == 201
    cid = created.json()["id"]

    listed = _client().get("/api/copilot/conversations").json()["items"]
    assert listed[0]["id"] == cid

    other = _client(user_id=OTHER).get(f"/api/copilot/conversations/{cid}")
    assert other.status_code == 404

    deleted = _client().delete(f"/api/copilot/conversations/{cid}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted_at"]
    assert _client().get("/api/copilot/conversations").json()["items"] == []
    still = _client().get(f"/api/copilot/conversations/{cid}")
    assert still.status_code == 200
    assert still.json()["deleted_at"]


def test_message_persists_assistant_dedupe_key(seeded, monkeypatch):
    async def fake_turn(history, principal):
        yield 'event: delta\ndata: {"text":"stock is 6"}\n\n'
        yield 'event: done\ndata: {"request_id":"dedupe-from-turn"}\n\n'

    monkeypatch.setattr("app.gateway._run_turn", fake_turn)
    cid = _client().post("/api/copilot/conversations", json={"facility_id": seeded["a"]}).json()["id"]
    res = _client().post(
        "/api/copilot/messages",
        json={"conversation_id": cid, "text": "How much norepinephrine do we have?"},
    )
    assert res.status_code == 200
    assert "stock is 6" in res.text
    body = _client().get(f"/api/copilot/conversations/{cid}").json()
    roles = [row["role"] for row in body["items"]]
    assert roles == ["user", "assistant"]
    assistant = body["items"][1]
    assert assistant["text"] == "stock is 6"
    assert assistant["ai_dedupe_key"] == "dedupe-from-turn"


def test_pagination_does_not_load_all_rows(seeded):
    cid = _client().post("/api/copilot/conversations", json={"facility_id": seeded["a"]}).json()["id"]
    with session_scope(str(HOSPITAL_A), str(ACTOR)) as s:
        convo = uuid.UUID(cid)
        for i in range(60):
            s.add(
                CopilotMessage(
                    conversation_id=convo,
                    hospital_id=HOSPITAL_A,
                    role="user" if i % 2 == 0 else "assistant",
                    text=f"m-{i}",
                )
            )
    page = _client().get(f"/api/copilot/conversations/{cid}", params={"limit": 20}).json()
    assert len(page["items"]) == 20
    oldest_kept = page["items"][0]["id"]
    older = _client().get(
        f"/api/copilot/conversations/{cid}", params={"limit": 20, "before": oldest_kept}
    ).json()
    assert len(older["items"]) == 20
    assert {row["id"] for row in page["items"]}.isdisjoint({row["id"] for row in older["items"]})
