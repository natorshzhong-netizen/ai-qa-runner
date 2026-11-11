from __future__ import annotations

from pathlib import Path

import pytest

from src.schema import (
    TestCase as CaseModel,
)
from src.schema import (
    dump_cases,
    load_cases,
    slugify_id,
    validate_case,
)

ROOT = Path(__file__).resolve().parents[1]


def _base(**overrides):
    payload = {
        "id": "case-ok",
        "title": "一个用例",
        "requirement_ref": "r.md#R1",
        "steps": [
            {"action": "goto", "url": "/"},
            {"action": "expect_visible", "target": {"by": "text", "value": "完成"}},
        ],
    }
    payload.update(overrides)
    return payload


def test_curated_demo_cases_are_valid():
    cases, issues, meta = load_cases(ROOT / "cases" / "demo-shop.json")
    assert issues == []
    assert len(cases) == 10
    assert meta["generated_by"].startswith("offline")


def test_minimal_case_is_valid():
    case, error = validate_case(_base())
    assert error is None
    assert case is not None
    assert case.priority == "P1"


@pytest.mark.parametrize(
    "overrides, hint",
    [
        ({"id": "Login-001"}, "id"),
        ({"steps": [{"action": "goto", "url": "/"}]}, "at least"),
        (
            {
                "steps": [
                    {"action": "goto", "url": "javascript:alert(1)"},
                    {"action": "expect_visible", "target": {"by": "text", "value": "x"}},
                ]
            },
            "goto",
        ),
        (
            {
                "steps": [
                    {"action": "fill", "target": {"by": "label", "value": "用户名"}},
                    {"action": "expect_visible", "target": {"by": "text", "value": "x"}},
                ]
            },
            "fill",
        ),
        (
            {
                "steps": [
                    {"action": "goto", "url": "/"},
                    {"action": "click", "target": {"by": "role", "role": "button"}},
                ]
            },
            "role",
        ),
        (
            {
                "steps": [
                    {"action": "goto", "url": "/"},
                    {"action": "click", "target": {"by": "css", "value": "#x", "role": "button"}},
                ]
            },
            "css",
        ),
    ],
)
def test_invalid_cases_are_rejected_with_reason(overrides, hint):
    case, error = validate_case(_base(**overrides))
    assert case is None
    assert error is not None
    assert hint.lower() in error.lower()


def test_unknown_field_is_rejected():
    case, error = validate_case(_base(selector="#submit"))
    assert case is None
    assert "selector" in (error or "")


def test_duplicate_ids_are_reported(tmp_path):
    path = tmp_path / "dupes.json"
    path.write_text(
        '{"cases": ['
        + ",".join([__import__("json").dumps(_base()), __import__("json").dumps(_base())])
        + "]}",
        encoding="utf-8",
    )
    cases, issues, _ = load_cases(path)
    assert len(cases) == 1
    assert len(issues) == 1
    assert "重复" in issues[0].message


def test_dump_and_reload_round_trip(tmp_path):
    case = CaseModel.model_validate(_base())
    path = tmp_path / "cases.json"
    path.write_text(dump_cases([case], {"generated_by": "unit-test"}), encoding="utf-8")
    loaded, issues, meta = load_cases(path)
    assert issues == []
    assert loaded[0].id == case.id
    assert meta["generated_by"] == "unit-test"


def test_slugify_handles_chinese_and_symbols():
    assert slugify_id("登录 成功") == "case"
    assert slugify_id("登录 成功", fallback="gen-01") == "gen-01"
    assert slugify_id("Login Valid") == "login-valid"
    assert slugify_id("!!!", fallback="gen-03") == "gen-03"
