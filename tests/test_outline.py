from __future__ import annotations

from src.outline import build_locator_index, check_case_locators
from src.schema import TestCase as CaseModel

OUTLINE = {
    "elements": [
        {
            "tag": "input",
            "role": "",
            "name": "用户名",
            "label": "用户名",
            "placeholder": "请输入用户名",
            "testid": "username",
        },
        {
            "tag": "button",
            "role": "",
            "name": "登录",
            "label": "",
            "placeholder": "",
            "testid": "login-button",
        },
    ],
    "headings": ["登录"],
    "alerts": ["请输入用户名"],
    "text": "登录 用户名 密码 欢迎回来",
}


def test_build_locator_index_collects_all_locator_kinds():
    index = build_locator_index(OUTLINE)
    assert "username" in index.testids
    assert "login-button" in index.testids
    assert "用户名" in index.labels
    assert "请输入用户名" in index.placeholders
    assert "button|登录" in index.role_names


def test_check_flags_only_missing_locators():
    case = CaseModel.model_validate(
        {
            "id": "locator-check",
            "title": "定位器核对",
            "requirement_ref": "r.md#R1",
            "steps": [
                {"action": "goto", "url": "/"},
                {"action": "fill", "target": {"by": "label", "value": "用户名"}, "value": "alice"},
                {"action": "click", "target": {"by": "role", "role": "button", "name": "登录"}},
                {"action": "expect_text", "target": {"by": "text", "value": "欢迎回来"}, "value": "欢迎"},
                {"action": "click", "target": {"by": "testid", "value": "missing"}},
            ],
        }
    )
    warnings = check_case_locators(case, build_locator_index(OUTLINE))
    assert len(warnings) == 1
    assert "missing" in warnings[0]
