import json

import pytest
from medstock_shared.ai import parse_model_json
from medstock_shared.ai_tasks import TASKS


def test_parse_model_json_plain_object():
    assert parse_model_json('{"items": [{"rxcui": "1"}]}') == {"items": [{"rxcui": "1"}]}


def test_parse_model_json_strips_markdown_fence():
    raw = '```json\n{"items": [{"rxcui": "1"}]}\n```'
    assert parse_model_json(raw) == {"items": [{"rxcui": "1"}]}


def test_parse_model_json_extracts_object_and_drops_trailing_comma():
    raw = 'noise\n{"items": [{"rxcui": "1"},],}\n'
    assert parse_model_json(raw) == {"items": [{"rxcui": "1"}]}


def test_parse_model_json_rejects_empty():
    with pytest.raises(json.JSONDecodeError):
        parse_model_json("   ")


def test_parse_model_json_rejects_array():
    with pytest.raises(json.JSONDecodeError):
        parse_model_json('[{"rxcui": "1"}]')


def test_analogue_prompt_does_not_ask_model_to_echo_source_text():
    prompt = TASKS["analogue"].prompt
    assert "Do not copy source_text" in prompt
    assert "Keep at most 5" in prompt
    assert "Copy source_text from the Source text section unchanged" not in prompt
