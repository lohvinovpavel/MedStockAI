from app.main import app
from fastapi.testclient import TestClient
from medstock_shared.ai_tasks import _citation_must_be_verbatim
from medstock_shared.auth import Principal, current_principal
from medstock_shared.config import settings

PHARMACIST = Principal("user-1", "hospital-1", "pharmacist")


def _client() -> TestClient:
    app.dependency_overrides[current_principal] = lambda: PHARMACIST
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_search_returns_list_even_for_one_hit(monkeypatch):
    monkeypatch.setattr(
        "app.main.search_concepts",
        lambda q, limit: [
            {
                "rxcui": "246461",
                "tty": "SCD",
                "name": "aspirin 100 MG Oral Tablet",
                "strength": "100 MG",
                "dose_form": "Oral Tablet",
                "_score": 100,
            }
        ],
    )
    monkeypatch.setattr("app.main.formulary_for", lambda principal: set())
    body = _client().get("/drugs/search", params={"q": "Aspirin 100 mg"}).json()
    assert body["query"] == "Aspirin 100 mg"
    assert isinstance(body["items"], list)
    assert len(body["items"]) == 1
    assert body["items"][0]["rxcui"] == "246461"
    assert body["items"][0]["in_formulary"] is False


def test_search_also_mounted_under_ingress_prefix(monkeypatch):
    monkeypatch.setattr("app.main.search_concepts", lambda q, limit: [])
    monkeypatch.setattr("app.main.formulary_for", lambda principal: set())
    res = _client().get("/api/analogue/drugs/search", params={"q": "x"})
    assert res.status_code == 200
    assert res.json() == {"query": "x", "items": []}


def test_search_requires_bearer_token():
    app.dependency_overrides.clear()
    res = TestClient(app).get("/drugs/search", params={"q": "aspirin"})
    assert res.status_code == 401
    assert res.json()["detail"] == "missing credentials"


def test_search_cookie_is_read_as_credentials():
    app.dependency_overrides.clear()
    client = TestClient(app)
    client.cookies.set("medstock_token", "not-a-jwt")
    res = client.get("/drugs/search", params={"q": "aspirin"})
    assert res.status_code == 401
    assert res.json()["detail"] == "invalid token"


def test_packages_lists_ndcs(monkeypatch):
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: ["00113041178", "00904201559"])
    monkeypatch.setattr("app.main.stock_totals_by_ndc", lambda principal, ndcs: {})
    body = _client().get("/drugs/246461/packages").json()
    assert body["rxcui"] == "246461"
    assert body["packages"] == [{"ndc": "00113041178"}, {"ndc": "00904201559"}]
    assert body["quantity"] == 0
    assert body["in_stock"] is False
    assert body["stock_status"] == "none"


def test_packages_include_source_stock_status(monkeypatch):
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: ["ndc-a"])
    monkeypatch.setattr("app.main.stock_totals_by_ndc", lambda principal, ndcs: {"ndc-a": 150})
    body = _client().get("/drugs/246461/packages").json()
    assert body["quantity"] == 150
    assert body["in_stock"] is True
    assert body["stock_status"] == "high"


def test_analogues_ranked_by_quantity_then_name(monkeypatch):
    monkeypatch.setattr(
        "app.main.related_scd_sbd",
        lambda rxcui: [
            {"rxcui": "low", "tty": "SCD", "name": "aspirin 81 MG Oral Tablet"},
            {"rxcui": "246461", "tty": "SCD", "name": "aspirin 100 MG Oral Tablet"},
            {"rxcui": "none-z", "tty": "SCD", "name": "aspirin chewable tablet"},
            {"rxcui": "none-a", "tty": "SCD", "name": "aspirin buffer tablet"},
            {"rxcui": "212033", "tty": "SCD", "name": "aspirin 325 MG Oral Tablet"},
        ],
    )
    ndcs = {
        "low": ["ndc-low"],
        "246461": ["ndc-246461"],
        "none-z": ["ndc-z"],
        "none-a": [],
        "212033": ["ndc-source"],
    }
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: ndcs.get(rxcui, []))
    monkeypatch.setattr(
        "app.main.stock_totals_by_ndc",
        lambda principal, found: {"ndc-246461": 120, "ndc-low": 4},
    )
    body = _client().get("/analogues/212033").json()
    assert body["rxcui"] == "212033"
    assert body["mode"] == "ingredient"
    assert body["use_ai"] is True
    assert body["rationale_unavailable"] is False
    assert [row["rxcui"] for row in body["items"]] == ["246461", "low", "none-a", "none-z"]
    assert body["items"][0] == {
        "rxcui": "246461",
        "tty": "SCD",
        "name": "aspirin 100 MG Oral Tablet",
        "quantity": 120,
        "in_stock": True,
        "stock_status": "high",
    }
    assert body["items"][1]["quantity"] == 4
    assert body["items"][1]["in_stock"] is True
    assert body["items"][1]["stock_status"] == "low"
    assert body["items"][2]["quantity"] == 0
    assert body["items"][2]["in_stock"] is False
    assert body["items"][2]["stock_status"] == "none"
    assert "212033" not in [row["rxcui"] for row in body["items"]]


def test_analogues_include_stock_status_none_ranked_last(monkeypatch):
    monkeypatch.setattr(
        "app.main.related_scd_sbd",
        lambda rxcui: [
            {"rxcui": "none-1", "tty": "SCD", "name": "aspirin chewable tablet"},
            {"rxcui": "high-1", "tty": "SCD", "name": "aspirin 100 MG Oral Tablet"},
            {"rxcui": "low-1", "tty": "SCD", "name": "aspirin 81 MG Oral Tablet"},
            {"rxcui": "normal-1", "tty": "SCD", "name": "aspirin 50 MG Oral Tablet"},
        ],
    )
    ndcs = {
        "none-1": ["ndc-none"],
        "high-1": ["ndc-high"],
        "low-1": ["ndc-low"],
        "normal-1": ["ndc-normal"],
    }
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: ndcs.get(rxcui, []))
    monkeypatch.setattr(
        "app.main.stock_totals_by_ndc",
        lambda principal, found: {"ndc-high": 150, "ndc-normal": 50, "ndc-low": 8},
    )
    body = _client().get("/analogues/212033").json()
    assert [row["stock_status"] for row in body["items"]] == ["high", "normal", "low", "none"]
    assert [row["rxcui"] for row in body["items"]] == ["high-1", "normal-1", "low-1", "none-1"]
    assert body["items"][-1]["quantity"] == 0
    assert body["items"][-1]["in_stock"] is False


def test_analogues_empty_list_is_valid(monkeypatch):
    monkeypatch.setattr("app.main.related_scd_sbd", lambda rxcui: [])
    body = _client().get("/analogues/212033").json()
    assert body == {
        "rxcui": "212033",
        "mode": "ingredient",
        "use_ai": True,
        "rationale_unavailable": False,
        "items": [],
    }


def test_analogues_mode_ingredient_is_default(monkeypatch):
    monkeypatch.setattr("app.main.related_scd_sbd", lambda rxcui: [])
    explicit = _client().get("/analogues/212033", params={"mode": "ingredient"})
    assert explicit.status_code == 200
    assert explicit.json()["mode"] == "ingredient"


def test_analogues_unimplemented_modes_are_422_not_fake_results():
    for mode in ("orange_book", "class"):
        res = _client().get("/analogues/212033", params={"mode": mode})
        assert res.status_code == 422, mode
        detail = res.json()["detail"]
        assert isinstance(detail, str), mode
        assert "not implemented" in detail.lower(), detail
        assert "items" not in res.json()


def test_analogues_full_ranked_by_quantity_excludes_same_ingredient(monkeypatch):
    monkeypatch.setattr(
        "app.main.therapeutic_scd_sbd",
        lambda rxcui: [
            {"rxcui": "197602", "tty": "SCD", "name": "diflunisal 250 MG Oral Tablet"},
            {"rxcui": "105798", "tty": "SCD", "name": "salsalate 500 MG Oral Capsule"},
            {"rxcui": "zero", "tty": "SCD", "name": "choline salicylate gel"},
            {"rxcui": "212033", "tty": "SCD", "name": "aspirin 325 MG Oral Tablet"},
        ],
    )
    ndcs = {
        "197602": ["ndc-dif"],
        "105798": ["ndc-sal"],
        "zero": ["ndc-zero"],
        "212033": ["ndc-source"],
    }
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: ndcs.get(rxcui, []))
    monkeypatch.setattr(
        "app.main.stock_totals_by_ndc",
        lambda principal, found: {"ndc-sal": 80, "ndc-dif": 12},
    )
    body = _client().get("/analogues/212033", params={"mode": "full", "use_ai": False}).json()
    assert body["rxcui"] == "212033"
    assert body["mode"] == "full"
    assert body["use_ai"] is False
    assert body["rationale_unavailable"] is False
    assert [row["rxcui"] for row in body["items"]] == ["105798", "197602", "zero"]
    assert body["items"][0]["quantity"] == 80
    assert body["items"][0]["in_stock"] is True
    assert body["items"][0]["stock_status"] == "normal"
    assert body["items"][2]["quantity"] == 0
    assert body["items"][2]["in_stock"] is False
    assert body["items"][2]["stock_status"] == "none"
    assert "212033" not in [row["rxcui"] for row in body["items"]]
    assert "246461" not in [row["rxcui"] for row in body["items"]]


def test_analogues_full_empty_class_is_valid(monkeypatch):
    monkeypatch.setattr("app.main.therapeutic_scd_sbd", lambda rxcui: [])
    body = _client().get("/analogues/212033", params={"mode": "full"}).json()
    assert body == {
        "rxcui": "212033",
        "mode": "full",
        "use_ai": True,
        "rationale_unavailable": False,
        "items": [],
    }


def test_analogues_full_mounted_under_ingress_prefix(monkeypatch):
    monkeypatch.setattr("app.main.therapeutic_scd_sbd", lambda rxcui: [])
    res = _client().get("/api/analogue/analogues/212033", params={"mode": "full"})
    assert res.status_code == 200
    assert res.json()["mode"] == "full"


def test_analogues_also_mounted_under_ingress_prefix(monkeypatch):
    monkeypatch.setattr("app.main.related_scd_sbd", lambda rxcui: [])
    res = _client().get("/api/analogue/analogues/212033")
    assert res.status_code == 200
    assert res.json() == {
        "rxcui": "212033",
        "mode": "ingredient",
        "use_ai": True,
        "rationale_unavailable": False,
        "items": [],
    }


def test_analogues_requires_bearer_token():
    app.dependency_overrides.clear()
    assert TestClient(app).get("/analogues/212033").status_code == 401


def _full_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main.therapeutic_scd_sbd",
        lambda rxcui: [
            {"rxcui": "197602", "tty": "SCD", "name": "diflunisal 250 MG Oral Tablet"},
            {"rxcui": "105798", "tty": "SCD", "name": "salsalate 500 MG Oral Capsule"},
            {"rxcui": "zero", "tty": "SCD", "name": "choline salicylate gel"},
        ],
    )
    ndcs = {"197602": ["ndc-dif"], "105798": ["ndc-sal"], "zero": ["ndc-zero"]}
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: ndcs.get(rxcui, []))
    monkeypatch.setattr(
        "app.main.stock_totals_by_ndc",
        lambda principal, found: {"ndc-sal": 80, "ndc-dif": 12},
    )
    monkeypatch.setattr(
        "app.main.concept_properties",
        lambda rxcui: {"name": "aspirin 325 MG Oral Tablet"},
    )


def test_use_ai_false_never_calls_ask_ai(monkeypatch):
    _full_candidates(monkeypatch)
    called = []

    def capture(payload):
        called.append(payload)
        return {"items": []}

    monkeypatch.setattr("app.main._ask_analogue_ai", capture)
    body = _client().get("/analogues/212033", params={"mode": "full", "use_ai": False}).json()
    assert called == []
    assert body["use_ai"] is False
    assert body["rationale_unavailable"] is False
    assert [row["rxcui"] for row in body["items"]] == ["105798", "197602", "zero"]
    assert "reason" not in body["items"][0]


def test_citation_mismatch_strips_quote_instead_of_rejecting_keep_set():
    result = {
        "source_text": "The source drug is aspirin 325 MG Oral Tablet, RxCUI 212033, which is in shortage.",
        "items": [
            {
                "rxcui": "197603",
                "rationale": "Same class NSAID.",
                "citation": "invented clinical sentence",
            },
            {
                "rxcui": "105798",
                "rationale": "Also a salicylate.",
                "citation": "which is in shortage.",
            },
        ],
    }
    _citation_must_be_verbatim(result)
    assert result["items"][0]["citation"] == ""
    assert result["items"][0]["rxcui"] == "197603"
    assert result["items"][1]["citation"] == "which is in shortage."


def test_ask_ai_error_falls_back_unfiltered_with_banner_flag(monkeypatch):
    _full_candidates(monkeypatch)

    def boom(_payload):
        raise RuntimeError("relation ai_cache does not exist")

    monkeypatch.setattr("app.main._ask_analogue_ai", boom)
    body = _client().get("/analogues/212033", params={"mode": "full", "use_ai": True}).json()
    assert body["use_ai"] is True
    assert body["rationale_unavailable"] is True
    assert [row["rxcui"] for row in body["items"]] == ["105798", "197602", "zero"]
    assert "reason" not in body["items"][0]


def test_empty_llm_keep_set_falls_back_to_unfiltered(monkeypatch):
    _full_candidates(monkeypatch)
    monkeypatch.setattr("app.main._ask_analogue_ai", lambda payload: {"items": []})
    body = _client().get("/analogues/212033", params={"mode": "full", "use_ai": True}).json()
    assert body["use_ai"] is True
    assert body["rationale_unavailable"] is True
    assert [row["rxcui"] for row in body["items"]] == ["105798", "197602", "zero"]
    assert "reason" not in body["items"][0]


def test_default_query_without_use_ai_param_is_200(monkeypatch):
    monkeypatch.setattr("app.main.related_scd_sbd", lambda rxcui: [])
    res = _client().get("/analogues/212033")
    assert res.status_code == 200
    assert res.json()["use_ai"] is True

    _full_candidates(monkeypatch)
    monkeypatch.setattr(
        "app.main._ask_analogue_ai",
        lambda payload: {
            "items": [
                {
                    "rxcui": "105798",
                    "rationale": "Same class oral NSAID.",
                    "citation": "not the same ingredient.",
                }
            ]
        },
    )
    full = _client().get("/analogues/212033", params={"mode": "full"})
    assert full.status_code == 200
    body = full.json()
    assert body["use_ai"] is True
    assert body["rationale_unavailable"] is False
    assert [row["rxcui"] for row in body["items"]] == ["105798"]
    assert body["items"][0]["reason"] == "Same class oral NSAID."


def test_ingredient_use_ai_true_never_calls_ask_ai(monkeypatch):
    monkeypatch.setattr("app.main.related_scd_sbd", lambda rxcui: [])
    called = []

    def capture(payload):
        called.append(payload)
        return {"items": []}

    monkeypatch.setattr("app.main._ask_analogue_ai", capture)
    body = _client().get("/analogues/212033", params={"use_ai": True}).json()
    assert called == []
    assert body["mode"] == "ingredient"
    assert body["items"] == []


def test_closed_world_unknown_rxcui_is_empty_keep_set_fallback(monkeypatch):
    _full_candidates(monkeypatch)
    monkeypatch.setattr(
        "app.main._ask_analogue_ai",
        lambda payload: {
            "items": [{"rxcui": "not-a-candidate", "rationale": "nope", "citation": "x"}]
        },
    )
    body = _client().get("/analogues/212033", params={"mode": "full"}).json()
    assert body["rationale_unavailable"] is True
    assert [row["rxcui"] for row in body["items"]] == ["105798", "197602", "zero"]


def _ten_full_candidates(monkeypatch) -> list[str]:
    """Ten Full-mode rows with mixed stock so LLM order ≠ UC-4 quantity order."""
    qtys = [200, 150, 110, 80, 50, 15, 5, 0, 0, 0]
    rows = []
    ndcs: dict[str, list[str]] = {}
    stock: dict[str, int] = {}
    ids: list[str] = []
    for i, qty in enumerate(qtys):
        rid = f"keep-{i}"
        ids.append(rid)
        rows.append({"rxcui": rid, "tty": "SCD", "name": f"analogue {i:02d}"})
        ndcs[rid] = [f"ndc-{i}"]
        if qty:
            stock[f"ndc-{i}"] = qty
    monkeypatch.setattr("app.main.therapeutic_scd_sbd", lambda rxcui: rows)
    monkeypatch.setattr("app.main.ndcs_for_rxcui", lambda rxcui: ndcs.get(rxcui, []))
    monkeypatch.setattr("app.main.stock_totals_by_ndc", lambda principal, found: stock)
    monkeypatch.setattr(
        "app.main.concept_properties",
        lambda rxcui: {"name": "aspirin 325 MG Oral Tablet"},
    )
    return ids


def test_use_ai_true_caps_keep_set_at_five_highest_quantity(monkeypatch):
    ids = _ten_full_candidates(monkeypatch)
    monkeypatch.setattr(
        "app.main._ask_analogue_ai",
        lambda payload: {
            "items": [
                {
                    "rxcui": rid,
                    "rationale": "commonly used",
                    "citation": "not the same ingredient.",
                }
                for rid in reversed(ids)
            ]
        },
    )
    body = _client().get("/analogues/212033", params={"mode": "full", "use_ai": True}).json()
    assert body["use_ai"] is True
    assert body["rationale_unavailable"] is False
    assert len(body["items"]) <= 5
    assert [row["rxcui"] for row in body["items"]] == ids[:5]
    assert [row["quantity"] for row in body["items"]] == [200, 150, 110, 80, 50]


def test_use_ai_false_full_list_can_exceed_five(monkeypatch):
    ids = _ten_full_candidates(monkeypatch)
    called = []

    def capture(payload):
        called.append(payload)
        return {"items": []}

    monkeypatch.setattr("app.main._ask_analogue_ai", capture)
    body = _client().get("/analogues/212033", params={"mode": "full", "use_ai": False}).json()
    assert called == []
    assert body["use_ai"] is False
    assert len(body["items"]) > 5
    assert [row["rxcui"] for row in body["items"]] == ids


def test_empty_keep_set_fallback_is_uncapped_unfiltered_list(monkeypatch):
    ids = _ten_full_candidates(monkeypatch)
    monkeypatch.setattr("app.main._ask_analogue_ai", lambda payload: {"items": []})
    body = _client().get("/analogues/212033", params={"mode": "full", "use_ai": True}).json()
    assert body["use_ai"] is True
    assert body["rationale_unavailable"] is True
    assert len(body["items"]) > 5
    assert [row["rxcui"] for row in body["items"]] == ids


def test_no_key_use_ai_true_is_409_not_unfiltered_list(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    _full_candidates(monkeypatch)
    called = []

    def capture(payload):
        called.append(payload)
        return {"items": [{"rxcui": "105798"}]}

    monkeypatch.setattr("app.main._ask_analogue_ai", capture)
    res = _client().get("/analogues/212033", params={"mode": "full", "use_ai": True})
    assert res.status_code == 409
    assert res.json()["detail"] == "AI is not configured"
    assert "items" not in res.json()
    assert called == []


def test_no_key_use_ai_false_is_200_unfiltered(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    _full_candidates(monkeypatch)
    called = []
    monkeypatch.setattr("app.main._ask_analogue_ai", lambda payload: called.append(payload))
    res = _client().get("/analogues/212033", params={"mode": "full", "use_ai": False})
    assert res.status_code == 200
    body = res.json()
    assert body["use_ai"] is False
    assert body["rationale_unavailable"] is False
    assert [row["rxcui"] for row in body["items"]] == ["105798", "197602", "zero"]
    assert called == []


def test_blank_gemini_key_use_ai_true_is_409(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "   ")
    res = _client().get("/analogues/212033", params={"mode": "full", "use_ai": True})
    assert res.status_code == 409
    assert res.json()["detail"] == "AI is not configured"


def test_no_key_omitted_use_ai_defaults_false(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    _full_candidates(monkeypatch)
    called = []
    monkeypatch.setattr("app.main._ask_analogue_ai", lambda payload: called.append(payload))
    res = _client().get("/analogues/212033", params={"mode": "full"})
    assert res.status_code == 200
    body = res.json()
    assert body["use_ai"] is False
    assert [row["rxcui"] for row in body["items"]] == ["105798", "197602", "zero"]
    assert called == []


def test_ai_status_true_when_key_present():
    res = _client().get("/analogues/ai-status")
    assert res.status_code == 200
    assert res.json() == {"available": True}


def test_ai_status_false_when_key_blank(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    res = _client().get("/analogues/ai-status")
    assert res.status_code == 200
    assert res.json() == {"available": False}


def test_ai_status_mounted_under_ingress_prefix(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    res = _client().get("/api/analogue/analogues/ai-status")
    assert res.status_code == 200
    assert res.json() == {"available": False}


def test_no_key_use_ai_true_also_409_on_ingress_prefix(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    res = _client().get(
        "/api/analogue/analogues/212033",
        params={"mode": "full", "use_ai": True},
    )
    assert res.status_code == 409
    assert res.json()["detail"] == "AI is not configured"

