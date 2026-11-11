from __future__ import annotations

import json

import pytest

from src.generate import (
    _dedupe,
    extract_cases_payload,
    generate_cases,
    normalize_item,
    offline_generate,
)


class FakeClient:
    label = "fake:test"

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return json.dumps(self.payload, ensure_ascii=False)


def _valid_case(case_id="good-case", locator=None):
    locator = locator or {"by": "text", "value": "完成"}
    return {
        "id": case_id,
        "title": f"用例 {case_id}",
        "requirement_ref": "r.md#R1",
        "steps": [
            {"action": "goto", "url": "/"},
            {"action": "expect_visible", "target": locator},
        ],
    }


def test_extract_plain_json_object():
    payload = extract_cases_payload('{"cases": [{"id": "a-1"}]}')
    assert payload == [{"id": "a-1"}]


def test_extract_fenced_json_with_prose():
    text = '结果如下：\n```json\n{"cases": [{"id": "b-2"}]}\n```\n以上。'
    assert extract_cases_payload(text) == [{"id": "b-2"}]


def test_extract_top_level_array():
    assert extract_cases_payload('[{"id": "c-3"}]') == [{"id": "c-3"}]


def test_extract_raises_on_garbage():
    with pytest.raises(ValueError):
        extract_cases_payload("抱歉，我无法生成。")


def test_normalize_fills_defaults_and_slugifies_id():
    item = normalize_item({"id": "Login Valid", "title": "登录"}, 0, "login.md")
    assert item["id"] == "login-valid"
    assert item["priority"] == "P1"
    assert item["type"] == "positive"
    assert item["requirement_ref"] == "login.md"


def test_normalize_chinese_id_falls_back_to_index():
    item = normalize_item({"title": "中文标题"}, 2, "r.md")
    assert item["id"] == "gen-03"


def test_dedupe_drops_duplicate_titles_and_suffixes_ids():
    items = [
        {"id": "same", "title": "标题"},
        {"id": "same", "title": "另一个标题"},
        {"id": "third", "title": "标题"},
    ]
    out = _dedupe(items)
    assert [i["id"] for i in out] == ["same", "same-2"]
    assert len(out) == 2


def test_offline_generate_reads_curated_cases():
    result = offline_generate("cases/demo-shop.json")
    assert result.provider == "offline"
    assert len(result.cases) == 10
    assert result.valid_rate == 1.0


def test_non_object_items_count_against_validity_rate():
    payload = {"cases": ["oops", _valid_case()]}
    client = FakeClient(payload)
    result = generate_cases("需求", client, repair_attempts=0)
    assert result.raw_items == 2
    assert len(result.cases) == 1
    assert result.valid_rate == 0.5
    assert any("JSON 对象" in issue.message for issue in result.issues)


def test_outline_marks_unknown_locator():
    outline = {
        "elements": [
            {"tag": "button", "role": "", "name": "登录", "testid": "login-button"},
        ],
        "headings": [],
        "alerts": [],
        "text": "登录 用户名 密码",
    }
    payload = {"cases": [_valid_case(locator={"by": "testid", "value": "not-in-outline"})]}
    result = generate_cases(
        "需求",
        FakeClient(payload),
        outline_text="页面摘要",
        outline=outline,
        repair_attempts=0,
    )
    assert len(result.cases) == 1
    assert result.outline_warnings
    assert "未出现在页面摘要中" in result.outline_warnings[0]
