"""测试用例的结构定义与校验。

LLM 生成的内容必须先过这一层，才能进入执行器。这里的规则同时决定
「用例有效率」这个指标：schema 不通过的用例不会被静默跳过，而是逐条记录原因。
"""

from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

CASE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")


class Action(str, Enum):
    """执行器认识的动作。新增动作必须同时补 executor 与提示词。"""

    GOTO = "goto"
    FILL = "fill"
    CLICK = "click"
    PRESS = "press"
    EXPECT_VISIBLE = "expect_visible"
    EXPECT_HIDDEN = "expect_hidden"
    EXPECT_TEXT = "expect_text"
    EXPECT_URL = "expect_url"
    EXPECT_VALUE = "expect_value"


class LocatorBy(str, Enum):
    ROLE = "role"
    LABEL = "label"
    TEXT = "text"
    PLACEHOLDER = "placeholder"
    TESTID = "testid"
    CSS = "css"


ASSERTION_ACTIONS = {
    Action.EXPECT_VISIBLE,
    Action.EXPECT_HIDDEN,
    Action.EXPECT_TEXT,
    Action.EXPECT_URL,
    Action.EXPECT_VALUE,
}


class Target(BaseModel):
    """一个定位器。优先语义定位（role/label/text/testid），css 只作兜底。"""

    model_config = ConfigDict(extra="forbid")

    by: LocatorBy
    value: str | None = None
    role: str | None = None
    name: str | None = None
    exact: bool = False

    @model_validator(mode="after")
    def _check(self) -> Target:
        if self.by == LocatorBy.ROLE:
            if not self.role or not self.name:
                raise ValueError("role 定位必须同时提供 role 和 name")
            if self.value:
                raise ValueError("role 定位使用 role/name，不要填 value")
        else:
            if not self.value or not self.value.strip():
                raise ValueError(f"{self.by.value} 定位必须提供非空 value")
            if self.role or self.name:
                raise ValueError(f"{self.by.value} 定位不要填 role/name")
        if self.exact and self.by in (LocatorBy.TESTID, LocatorBy.CSS):
            raise ValueError("testid/css 不支持 exact")
        return self

    def describe(self) -> str:
        if self.by == LocatorBy.ROLE:
            return f'role={self.role} name="{self.name}"'
        return f'{self.by.value}="{self.value}"'


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action
    target: Target | None = None
    value: str | None = None
    url: str | None = None
    description: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Step:
        a = self.action
        if a == Action.GOTO:
            if not self.url:
                raise ValueError("goto 必须提供 url")
            if self.target is not None:
                raise ValueError("goto 不接受 target")
            url = self.url.strip()
            if not url.startswith(("http://", "https://", "/", "./", "../")):
                raise ValueError("goto 的 url 必须是 http(s) 或以 / . 开头的相对路径")
        elif a == Action.CLICK:
            if self.target is None:
                raise ValueError("click 必须提供 target")
        elif a == Action.FILL:
            if self.target is None:
                raise ValueError("fill 必须提供 target")
            if self.value is None:
                raise ValueError("fill 必须提供 value（空字符串表示清空）")
        elif a == Action.PRESS:
            if self.target is None:
                raise ValueError("press 必须提供 target")
            if not self.value:
                raise ValueError("press 必须提供按键名，例如 Enter")
        elif a == Action.EXPECT_URL:
            if not self.value:
                raise ValueError("expect_url 必须提供 value（URL 片段）")
            if self.target is not None:
                raise ValueError("expect_url 不接受 target")
        elif a in (Action.EXPECT_TEXT, Action.EXPECT_VALUE):
            if self.target is None:
                raise ValueError(f"{a.value} 必须提供 target")
            if self.value is None:
                raise ValueError(f"{a.value} 必须提供 value")
        elif a in (Action.EXPECT_VISIBLE, Action.EXPECT_HIDDEN):
            if self.target is None:
                raise ValueError(f"{a.value} 必须提供 target")
        return self

    def describe(self) -> str:
        if self.description:
            return self.description
        if self.action == Action.GOTO:
            return f"打开 {self.url}"
        if self.action == Action.FILL:
            return f"在 {self.target.describe()} 填入 {self.value!r}"  # type: ignore[union-attr]
        if self.action == Action.CLICK:
            return f"点击 {self.target.describe()}"  # type: ignore[union-attr]
        if self.action == Action.PRESS:
            return f"在 {self.target.describe()} 按 {self.value}"  # type: ignore[union-attr]
        if self.action == Action.EXPECT_URL:
            return f"URL 包含 {self.value!r}"
        target = self.target.describe() if self.target else ""
        mapping = {
            Action.EXPECT_VISIBLE: f"{target} 可见",
            Action.EXPECT_HIDDEN: f"{target} 不可见",
            Action.EXPECT_TEXT: f"{target} 包含文本 {self.value!r}",
            Action.EXPECT_VALUE: f"{target} 的值为 {self.value!r}",
        }
        return mapping.get(self.action, self.action.value)


class TestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str = Field(min_length=2, max_length=80)
    requirement_ref: str = Field(min_length=1, max_length=200)
    priority: Literal["P0", "P1", "P2"] = "P1"
    type: Literal["positive", "negative", "edge", "regression"] = "positive"
    preconditions: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=2)
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> TestCase:
        if not CASE_ID_RE.match(self.id):
            raise ValueError("id 必须是 3–64 位小写字母/数字/连字符，且以字母开头")
        if self.steps[0].action != Action.GOTO:
            raise ValueError("每个用例必须从 goto 开始（用例之间不共享上下文）")
        if not any(s.action in ASSERTION_ACTIONS for s in self.steps):
            raise ValueError("每个用例至少包含一个 expect_* 断言")
        return self


class ValidationIssue(BaseModel):
    index: int
    case_id: str | None = None
    message: str


class CaseFile(BaseModel):
    """用例文件的元信息。cases 单独校验，一条坏用例不影响其余用例。"""

    version: int = 1
    generated_by: str | None = None
    source_url: str | None = None
    requirement_ref: str | None = None
    cases: list[dict[str, Any]] = Field(default_factory=list)


def format_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors()[:4]:
        loc = ".".join(str(x) for x in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts)


def validate_case(raw: Any) -> tuple[TestCase | None, str | None]:
    """校验单条用例，返回 (用例, 错误信息)。"""
    if not isinstance(raw, dict):
        return None, "用例必须是 JSON 对象"
    try:
        return TestCase.model_validate(raw), None
    except ValidationError as exc:
        return None, format_error(exc)


def _extract_items(data: Any) -> tuple[list[Any], dict[str, Any]]:
    if isinstance(data, list):
        return data, {}
    if isinstance(data, dict):
        if "cases" not in data:
            raise ValueError("JSON 对象里没有 cases 字段")
        meta = {k: v for k, v in data.items() if k != "cases"}
        if not isinstance(data["cases"], list):
            raise ValueError("cases 必须是数组")
        return data["cases"], meta
    raise ValueError("用例文件必须是数组或含 cases 字段的对象")


def load_cases(path: str | Path) -> tuple[list[TestCase], list[ValidationIssue], dict[str, Any]]:
    """读取用例文件，逐条校验。返回 (合法用例, 问题列表, 元信息)。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items, meta = _extract_items(raw)
    valid: list[TestCase] = []
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        case, err = validate_case(item)
        if err:
            issues.append(ValidationIssue(index=index, case_id=_peek_id(item), message=err))
            continue
        assert case is not None
        if case.id in seen:
            issues.append(ValidationIssue(index=index, case_id=case.id, message=f"重复的用例 id: {case.id}"))
            continue
        seen.add(case.id)
        valid.append(case)
    return valid, issues, meta


def _peek_id(item: Any) -> str | None:
    if isinstance(item, dict) and isinstance(item.get("id"), str):
        return item["id"]
    return None


def validate_raw_list(items: list[Any]) -> tuple[list[TestCase], list[ValidationIssue]]:
    """给 generator / eval 用：对内存中的列表逐条校验。"""
    valid: list[TestCase] = []
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        case, err = validate_case(item)
        if err:
            issues.append(ValidationIssue(index=index, case_id=_peek_id(item), message=err))
            continue
        assert case is not None
        if case.id in seen:
            issues.append(ValidationIssue(index=index, case_id=case.id, message=f"重复的用例 id: {case.id}"))
            continue
        seen.add(case.id)
        valid.append(case)
    return valid, issues


def slugify_id(text: str, fallback: str = "case") -> str:
    """把模型可能给出的任意 id 归一成合法 slug。"""
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not ascii_slug:
        ascii_slug = fallback
    if not ascii_slug[0].isalpha():
        ascii_slug = f"c-{ascii_slug}"
    return ascii_slug[:64].strip("-") or fallback


def dump_cases(cases: list[TestCase], meta: dict[str, Any] | None = None) -> str:
    payload: dict[str, Any] = {"version": 1}
    if meta:
        payload.update(meta)
    payload["cases"] = [c.model_dump(mode="json", exclude_none=True) for c in cases]
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
