from unittest.mock import MagicMock

from app.main import load_formulary_rxcuis
from medstock_shared.rxnorm import (
    apply_formulary,
    parse_strength_and_form,
    related_scd_sbd,
    search_concepts,
    therapeutic_scd_sbd,
)
from sqlalchemy.exc import ProgrammingError


def test_parse_scd_name():
    strength, form = parse_strength_and_form("aspirin 100 MG Oral Tablet")
    assert strength == "100 MG"
    assert form == "Oral Tablet"


def test_apply_formulary_sorts_without_changing_shape():
    hits = [
        {
            "rxcui": "1",
            "tty": "SCD",
            "name": "zzz",
            "strength": "10 MG",
            "dose_form": "Oral Tablet",
            "_score": 90,
        },
        {
            "rxcui": "2",
            "tty": "SCD",
            "name": "aaa",
            "strength": "10 MG",
            "dose_form": "Oral Tablet",
            "_score": 10,
        },
    ]
    items = apply_formulary(hits, {"2"}, limit=20)
    assert [row["rxcui"] for row in items] == ["2", "1"]
    assert items[0]["in_formulary"] is True
    assert items[1]["in_formulary"] is False
    assert "_score" not in items[0]
    assert set(items[0]) == {
        "rxcui",
        "tty",
        "name",
        "strength",
        "dose_form",
        "in_formulary",
    }


def test_apply_formulary_preserves_search_order_when_not_on_formulary():
    hits = [
        {
            "rxcui": "212033",
            "tty": "SCD",
            "name": "aspirin 325 MG Oral Tablet",
            "strength": "325 MG",
            "dose_form": "Oral Tablet",
            "_score": 100,
        },
        {
            "rxcui": "994237",
            "tty": "SCD",
            "name": "aspirin 325 MG / butalbital 50 MG / caffeine 40 MG / codeine phosphate 30 MG Oral Capsule",
            "strength": "325 MG, 50 MG, 40 MG, 30 MG",
            "dose_form": "Oral Capsule",
            "_score": 100,
        },
    ]
    items = apply_formulary(hits, set(), limit=20)
    assert [row["rxcui"] for row in items] == ["212033", "994237"]


def test_load_formulary_missing_table_is_empty():
    session = MagicMock()
    session.scalars.side_effect = ProgrammingError("select", {}, Exception("undefined"))
    assert load_formulary_rxcuis(session) == set()
    session.rollback.assert_called_once()


def test_ndcs_uses_curated_when_live_empty(monkeypatch):
    from medstock_shared import rxnorm as rx

    rx._CACHE.clear()

    def fake_get(path, params=None):
        if path == "/rxcui/246461/ndcs.json":
            return {"ndcGroup": {"rxcui": None, "ndcList": {}}}
        raise AssertionError(path)

    monkeypatch.setattr(rx, "_get", fake_get)
    ndcs = rx.ndcs_for_rxcui("246461")
    assert ndcs == rx.CURATED_NDCS_WHEN_EMPTY["246461"]
    assert "00113041178" in ndcs


def test_search_lifts_scdc_when_drugs_json_empty(monkeypatch):
    from medstock_shared import rxnorm as rx

    rx._CACHE.clear()

    def fake_get(path, params=None):
        if path.startswith("/drugs.json"):
            return {"drugGroup": {"conceptGroup": []}}
        if path.startswith("/approximateTerm.json"):
            return {
                "approximateGroup": {
                    "candidate": [
                        {
                            "rxcui": "329292",
                            "score": "9.8",
                            "name": "aspirin 100 MG",
                            "tty": "SCDC",
                        }
                    ]
                }
            }
        if "related.json" in path:
            return {
                "relatedGroup": {
                    "conceptGroup": [
                        {
                            "tty": "SCD",
                            "conceptProperties": [
                                {
                                    "rxcui": "246461",
                                    "name": "aspirin 100 MG Oral Tablet",
                                    "tty": "SCD",
                                }
                            ],
                        }
                    ]
                }
            }
        raise AssertionError(path)

    monkeypatch.setattr(rx, "_get", fake_get)
    hits = search_concepts("Aspirin 100 mg", limit=20)
    assert hits[0]["rxcui"] == "246461"
    assert hits[0]["tty"] == "SCD"
    assert hits[0]["strength"] == "100 MG"


def test_related_scd_sbd_lifts_ingredient_and_excludes_source(monkeypatch):
    from medstock_shared import rxnorm as rx

    rx._CACHE.clear()

    def fake_get(path, params=None):
        if path == "/rxcui/212033/related.json?tty=IN":
            return {
                "relatedGroup": {
                    "conceptGroup": [
                        {
                            "tty": "IN",
                            "conceptProperties": [{"rxcui": "1191", "name": "aspirin", "tty": "IN"}],
                        }
                    ]
                }
            }
        if path == "/rxcui/212033/related.json?tty=SCD+SBD":
            return {
                "relatedGroup": {
                    "conceptGroup": [
                        {
                            "tty": "SBD",
                            "conceptProperties": [
                                {
                                    "rxcui": "209459",
                                    "name": "aspirin 325 MG Oral Tablet [Bayer]",
                                    "tty": "SBD",
                                }
                            ],
                        }
                    ]
                }
            }
        if path == "/rxcui/1191/related.json?tty=SCD+SBD":
            return {
                "relatedGroup": {
                    "conceptGroup": [
                        {
                            "tty": "SCD",
                            "conceptProperties": [
                                {
                                    "rxcui": "212033",
                                    "name": "aspirin 325 MG Oral Tablet",
                                    "tty": "SCD",
                                },
                                {
                                    "rxcui": "246461",
                                    "name": "aspirin 100 MG Oral Tablet",
                                    "tty": "SCD",
                                },
                                {
                                    "rxcui": "243670",
                                    "name": "aspirin 81 MG Oral Tablet",
                                    "tty": "SCD",
                                },
                            ],
                        }
                    ]
                }
            }
        raise AssertionError(path)

    monkeypatch.setattr(rx, "_get", fake_get)
    items = related_scd_sbd("212033")
    rxcuis = [row["rxcui"] for row in items]
    assert "212033" not in rxcuis
    assert rxcuis == ["246461", "243670", "209459"]
    assert "_score" not in items[0]


def test_related_scd_sbd_caps_and_empty_is_valid(monkeypatch):
    from medstock_shared import rxnorm as rx

    rx._CACHE.clear()

    def fake_get(path, params=None):
        if path.endswith("related.json?tty=IN"):
            return {"relatedGroup": {"conceptGroup": []}}
        if path.endswith("related.json?tty=SCD+SBD"):
            props = [
                {"rxcui": str(i), "name": f"drug {i:02d}", "tty": "SCD"} for i in range(50)
            ]
            return {"relatedGroup": {"conceptGroup": [{"tty": "SCD", "conceptProperties": props}]}}
        raise AssertionError(path)

    monkeypatch.setattr(rx, "_get", fake_get)
    items = related_scd_sbd("999", limit=30)
    assert len(items) == 30
    rx._CACHE.clear()
    monkeypatch.setattr(
        rx,
        "_get",
        lambda path, params=None: {"relatedGroup": {"conceptGroup": []}},
    )
    assert related_scd_sbd("1") == []


def test_therapeutic_scd_sbd_excludes_same_ingredient(monkeypatch):
    from medstock_shared import rxnorm as rx

    rx._CACHE.clear()

    def fake_get(path, params=None):
        params = params or {}
        if path == "/rxclass/class/byRxcui.json":
            return {
                "rxclassDrugInfoList": {
                    "rxclassDrugInfo": [
                        {
                            "minConcept": {
                                "rxcui": "212033",
                                "name": "aspirin 325 MG Oral Tablet",
                                "tty": "SCD",
                            },
                            "rxclassMinConceptItem": {
                                "classId": "N02BA",
                                "className": "Salicylic acid and derivatives",
                                "classType": "ATC1-4",
                            },
                            "rela": "",
                            "relaSource": "ATCPROD",
                        }
                    ]
                }
            }
        if path == "/rxclass/classMembers.json":
            assert params.get("classId") == "N02BA"
            assert params.get("relaSource") == "ATCPROD"
            return {
                "drugMemberGroup": {
                    "drugMember": [
                        {
                            "minConcept": {
                                "rxcui": "212033",
                                "name": "aspirin 325 MG Oral Tablet",
                                "tty": "SCD",
                            }
                        },
                        {
                            "minConcept": {
                                "rxcui": "246461",
                                "name": "aspirin 100 MG Oral Tablet",
                                "tty": "SCD",
                            }
                        },
                        {
                            "minConcept": {
                                "rxcui": "197602",
                                "name": "diflunisal 250 MG Oral Tablet",
                                "tty": "SCD",
                            }
                        },
                        {
                            "minConcept": {
                                "rxcui": "105798",
                                "name": "salsalate 500 MG Oral Capsule",
                                "tty": "SCD",
                            }
                        },
                    ]
                }
            }
        if path == "/rxcui/212033/related.json?tty=IN":
            return {
                "relatedGroup": {
                    "conceptGroup": [
                        {
                            "tty": "IN",
                            "conceptProperties": [{"rxcui": "1191", "name": "aspirin", "tty": "IN"}],
                        }
                    ]
                }
            }
        if path.endswith("related.json?tty=SCD+SBD"):
            return {
                "relatedGroup": {
                    "conceptGroup": [
                        {
                            "tty": "SCD",
                            "conceptProperties": [
                                {
                                    "rxcui": "212033",
                                    "name": "aspirin 325 MG Oral Tablet",
                                    "tty": "SCD",
                                },
                                {
                                    "rxcui": "246461",
                                    "name": "aspirin 100 MG Oral Tablet",
                                    "tty": "SCD",
                                },
                            ],
                        }
                    ]
                }
            }
        raise AssertionError((path, params))

    monkeypatch.setattr(rx, "_get", fake_get)
    items = therapeutic_scd_sbd("212033")
    rxcuis = [row["rxcui"] for row in items]
    assert "212033" not in rxcuis
    assert "246461" not in rxcuis
    assert rxcuis == ["197602", "105798"]


def test_search_ranks_simple_prep_above_combinations(monkeypatch):
    from medstock_shared import rxnorm as rx

    rx._CACHE.clear()

    def fake_get(path, params=None):
        if path.startswith("/drugs.json"):
            return {
                "drugGroup": {
                    "conceptGroup": [
                        {
                            "tty": "SCD",
                            "conceptProperties": [
                                {
                                    "rxcui": "994237",
                                    "name": "aspirin 325 MG / butalbital 50 MG / caffeine 40 MG / codeine phosphate 30 MG Oral Capsule",
                                    "tty": "SCD",
                                },
                                {
                                    "rxcui": "212033",
                                    "name": "aspirin 325 MG Oral Tablet",
                                    "tty": "SCD",
                                },
                                {
                                    "rxcui": "238134",
                                    "name": "aspirin 325 MG / butalbital 50 MG / caffeine 40 MG Oral Capsule",
                                    "tty": "SCD",
                                },
                            ],
                        }
                    ]
                }
            }
        raise AssertionError(path)

    monkeypatch.setattr(rx, "_get", fake_get)
    hits = search_concepts("aspirin 325", limit=20)
    assert [row["rxcui"] for row in hits] == ["212033", "238134", "994237"]


def test_therapeutic_tries_next_class_when_same_ingredient_empties(monkeypatch):
    from medstock_shared import rxnorm as rx

    rx._CACHE.clear()

    def fake_get(path, params=None):
        params = params or {}
        if path == "/rxclass/class/byRxcui.json":
            return {
                "rxclassDrugInfoList": {
                    "rxclassDrugInfo": [
                        {
                            "minConcept": {"rxcui": "763116", "name": "combo", "tty": "SCD"},
                            "rxclassMinConceptItem": {
                                "classId": "N02BE",
                                "className": "Anilides",
                                "classType": "ATC1-4",
                            },
                            "rela": "",
                            "relaSource": "ATCPROD",
                        },
                        {
                            "minConcept": {"rxcui": "763116", "name": "combo", "tty": "SCD"},
                            "rxclassMinConceptItem": {
                                "classId": "CN103",
                                "className": "NON-OPIOID ANALGESICS",
                                "classType": "VA",
                            },
                            "rela": "has_VAClass",
                            "relaSource": "VA",
                        },
                    ]
                }
            }
        if path == "/rxclass/classMembers.json":
            if params.get("classId") == "N02BE":
                return {
                    "drugMemberGroup": {
                        "drugMember": [
                            {
                                "minConcept": {
                                    "rxcui": "763116",
                                    "name": "acetaminophen 260 MG / aspirin 520 MG / caffeine 32.5 MG Oral Powder",
                                    "tty": "SCD",
                                }
                            },
                            {
                                "minConcept": {
                                    "rxcui": "198440",
                                    "name": "acetaminophen 325 MG Oral Tablet",
                                    "tty": "SCD",
                                }
                            },
                        ]
                    }
                }
            if params.get("classId") == "CN103":
                return {
                    "drugMemberGroup": {
                        "drugMember": [
                            {
                                "minConcept": {
                                    "rxcui": "197603",
                                    "name": "diflunisal 500 MG Oral Tablet",
                                    "tty": "SCD",
                                }
                            },
                            {
                                "minConcept": {
                                    "rxcui": "198440",
                                    "name": "acetaminophen 325 MG Oral Tablet",
                                    "tty": "SCD",
                                }
                            },
                        ]
                    }
                }
            raise AssertionError(params)
        if path.endswith("related.json?tty=IN"):
            return {
                "relatedGroup": {
                    "conceptGroup": [
                        {
                            "tty": "IN",
                            "conceptProperties": [
                                {"rxcui": "161", "name": "acetaminophen", "tty": "IN"},
                                {"rxcui": "1191", "name": "aspirin", "tty": "IN"},
                            ],
                        }
                    ]
                }
            }
        if path.endswith("related.json?tty=SCD+SBD"):
            return {
                "relatedGroup": {
                    "conceptGroup": [
                        {
                            "tty": "SCD",
                            "conceptProperties": [
                                {
                                    "rxcui": "763116",
                                    "name": "acetaminophen 260 MG / aspirin 520 MG / caffeine 32.5 MG Oral Powder",
                                    "tty": "SCD",
                                },
                                {
                                    "rxcui": "198440",
                                    "name": "acetaminophen 325 MG Oral Tablet",
                                    "tty": "SCD",
                                },
                            ],
                        }
                    ]
                }
            }
        raise AssertionError((path, params))

    monkeypatch.setattr(rx, "_get", fake_get)
    items = therapeutic_scd_sbd("763116")
    assert [row["rxcui"] for row in items] == ["197603"]


def test_therapeutic_scd_sbd_empty_class_is_valid(monkeypatch):
    from medstock_shared import rxnorm as rx

    rx._CACHE.clear()
    monkeypatch.setattr(rx, "_get", lambda path, params=None: {})
    assert therapeutic_scd_sbd("212033") == []
