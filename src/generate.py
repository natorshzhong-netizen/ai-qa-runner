"""需求 → 结构化用例。生成、校验、按错误信息修复，最多 N 轮。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .llm import LLMClient
from .outline import build_locator_index, check_case_locators
from .schema import (
    TestCase,
    ValidationIssue,
    dump_cases,
    slugify_id,
    validate_raw_list,
)

SYSTEM_PROMPT = """你是一名资深测试工程师，负责把需求文档转成可以直接执行的端到端测试用例。

只输出一个 JSON 对象，结构如下（不要 Markdown 代码块，不要解释）：
{"cases": [CASE, ...]}

CASE 的字段：
- id: 3-64 位小写字母/数字/连字符，以字母开头，例如 login-valid-account
- title: 一句中文，说明验证什么
- requirement_ref: 写成「文件名#条目编号」，例如 login.md#R1；没有编号就写原文片段
- priority: P0 / P1 / P2
- type: positive / negative / edge / regression
- preconditions: 字符串数组，没有就给 []
- steps: STEP 数组，至少 2 步，第一步必须是 goto
- tags: 字符串数组，例如 ["登录", "表单校验"]

STEP 的字段：
- action: goto | fill | click | press | expect_visible | expect_hidden | expect_text | expect_url | expect_value
- url: 仅 goto 使用，可以是相对路径，例如 "/"
- target: 定位器（除 goto / expect_url 外必填）
- value: fill/press/expect_text/expect_url/expect_value 使用
- description: 可选，一句中文说明这一步做什么

定位器 target 的形状（by 必填）：
- {"by": "role", "role": "button", "name": "登录"}      ← 首选
- {"by": "label", "value": "用户名"}                   ← 表单输入首选
- {"by": "testid", "value": "cart-total"}              ← 页面提供了 data-testid 时首选
- {"by": "placeholder", "value": "搜索商品"}
- {"by": "text", "value": "欢迎回来", "exact": false}
- {"by": "css", "value": "#login"}                     ← 最后手段

硬性规则：
1. 每个用例互相独立：都必须从 goto 开始，不能假设上一个用例登录过或购物车里有东西。
   如果需求描述的是登录后的页面，用例必须自己先完成登录，不能直接操作商城里才有的按钮。
2. 每个用例至少一个 expect_* 断言；断言要检查需求里写明的可观察结果，不要只检查"元素存在"。
3. 只使用页面上真实存在的元素。优先引用「页面可达性摘要」里的 name / label / testid；
   摘要里没有的元素不要编造 testid 或 css。
4. 负向/边界用例必须覆盖需求中提到的错误提示、必填校验或边界值。
5. 一份需求通常需要 4-8 条用例，覆盖正向、负向、边界；不要为了凑数重复同一条。
6. 测试数据写死在 value 里；需求没给的数据不要臆造真实账号。
"""

REPAIR_PROMPT = """下面是你上一次生成的用例 JSON。它没有通过 schema 校验。

校验错误：
{issues}

上一次输出：
{previous}

请修正所有错误，输出完整的新 JSON（格式同前）。要求：
- 只输出 JSON 对象本身，不要解释。
- 保持原有的用例意图，不要因为校验失败就删除整条用例；能修就修。
- 仍然遵守所有硬性规则（每个用例从 goto 开始、至少一个 expect_*、定位器字段合法）。
"""


@dataclass
class GenerationResult:
    cases: list[TestCase]
    issues: list[ValidationIssue]
    attempts: int
    provider: str
    raw_items: int = 0
    outline_used: bool = False
    outline_warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def valid_rate(self) -> float:
        return len(self.cases) / self.raw_items if self.raw_items else 0.0


def extract_cases_payload(text: str) -> list[Any]:
    """从模型输出里抠出 cases 数组，容忍代码块与前后废话。"""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char not in "[{":
            continue
        try:
            obj, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict) and isinstance(obj.get("cases"), list):
            return obj["cases"]
    raise ValueError("模型输出里找不到合法的 cases 数组")


def normalize_item(item: Any, index: int, requirement_ref: str) -> Any:
    """补齐缺省字段、把 id 归一成合法 slug。不掩盖结构性错误。"""
    if not isinstance(item, dict):
        return item
    data = dict(item)
    data.setdefault("requirement_ref", requirement_ref)
    data.setdefault("priority", "P1")
    data.setdefault("type", "positive")
    data.setdefault("preconditions", [])
    data.setdefault("tags", [])

    title = str(data.get("title") or "").strip()
    raw_id = str(data.get("id") or "").strip()
    candidate = slugify_id(raw_id or title, fallback=f"gen-{index + 1:02d}")
    if candidate in ("case", "gen"):
        candidate = f"gen-{index + 1:02d}"
    data["id"] = candidate
    return data


def _dedupe(items: list[Any]) -> list[Any]:
    """同一次生成里重复的 id / 标题只保留第一条，后面的加后缀或丢弃。

    非对象条目原样放行，交给 validate_raw_list 产出「用例必须是 JSON 对象」，
    这样它们会计入 raw_items 并触发修复，而不是被静默丢掉。
    """
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    out: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            out.append(item)
            continue
        title = " ".join(str(item.get("title", "")).split()).lower()
        if title and title in seen_titles:
            continue
        case_id = str(item.get("id", ""))
        if case_id in seen_ids:
            suffix = 2
            while f"{case_id}-{suffix}" in seen_ids:
                suffix += 1
            case_id = f"{case_id}-{suffix}"
            item["id"] = case_id
        seen_ids.add(case_id)
        if title:
            seen_titles.add(title)
        out.append(item)
    return out


def build_messages(requirement: str, outline_text: str, count: int, requirement_source: str) -> list[dict]:
    user = f"""需求来源：{requirement_source}
需要的用例数量：约 {count} 条

【需求文档】
{requirement.strip()[:8000]}

【页面可达性摘要】
{outline_text.strip()[:6000] if outline_text.strip() else "（未提供；请只使用需求中明确描述的元素，优先 role/label 语义定位）"}

请输出 JSON。"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _attempt(
    client: LLMClient,
    messages: list[dict],
    requirement_ref: str,
) -> tuple[list[TestCase], list[ValidationIssue], list[Any]]:
    raw_text = client.chat(messages, json_mode=True, temperature=0.2)
    items = extract_cases_payload(raw_text)
    normalized = [normalize_item(item, i, requirement_ref) for i, item in enumerate(items)]
    normalized = _dedupe(normalized)
    valid, issues = validate_raw_list(normalized)
    return valid, issues, normalized


def generate_cases(
    requirement: str,
    client: LLMClient,
    *,
    outline_text: str = "",
    outline: dict[str, Any] | None = None,
    count: int = 6,
    requirement_source: str = "requirements.md",
    repair_attempts: int = 2,
    log: Callable[[str], None] | None = None,
) -> GenerationResult:
    requirement_ref = Path(requirement_source).name
    messages = build_messages(requirement, outline_text, count, requirement_source)
    best: tuple[list[TestCase], list[ValidationIssue], int, int] | None = None
    attempts = 0

    for attempt in range(repair_attempts + 1):
        attempts = attempt + 1
        if log:
            log(f"第 {attempts} 轮生成…")
        try:
            valid, issues, normalized = _attempt(client, messages, requirement_ref)
        except (ValueError, json.JSONDecodeError) as exc:
            issues = [ValidationIssue(index=0, message=f"输出不是合法 JSON: {exc}")]
            normalized = []
            valid = []
        if log:
            log(f"  合法 {len(valid)} / {len(normalized)} 条（问题 {len(issues)} 条）")

        score = (len(valid), -len(issues))
        if best is None or score > (len(best[0]), -len(best[1])):
            best = (valid, issues, len(normalized), attempts)

        if not issues and valid:
            break
        if attempt >= repair_attempts:
            break

        previous = json.dumps({"cases": normalized}, ensure_ascii=False)[:12000]
        issue_text = "\n".join(f"- 第 {i.index + 1} 条: {i.message}" for i in issues[:12]) or "- 输出为空"
        messages = messages + [
            {"role": "assistant", "content": previous},
            {
                "role": "user",
                "content": REPAIR_PROMPT.format(issues=issue_text, previous=previous),
            },
        ]

    assert best is not None
    valid, issues, raw_count, best_attempt = best
    outline_warnings: list[str] = []
    if outline is not None and valid:
        index = build_locator_index(outline)
        for case in valid:
            outline_warnings.extend(check_case_locators(case, index))
    return GenerationResult(
        cases=valid,
        issues=issues,
        attempts=best_attempt,
        provider=client.label,
        raw_items=raw_count,
        outline_used=bool(outline_text.strip()),
        outline_warnings=outline_warnings,
    )


def offline_generate(path: str | Path) -> GenerationResult:
    """没有模型时的确定性路径：读仓库自带的用例。"""
    from .schema import load_cases

    cases, issues, _meta = load_cases(path)
    return GenerationResult(
        cases=cases,
        issues=issues,
        attempts=1,
        provider="offline",
        raw_items=len(cases) + len(issues),
        outline_used=False,
        notes=[f"offline：直接读取 {Path(path).as_posix()}"],
    )


def save_result(result: GenerationResult, out: str | Path, meta: dict[str, Any] | None = None) -> None:
    payload_meta = {
        "generated_by": result.provider,
        "attempts": result.attempts,
    }
    if meta:
        payload_meta.update(meta)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(dump_cases(result.cases, payload_meta), encoding="utf-8")
