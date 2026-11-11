"""页面可达性探测：先把页面上真实存在的元素/标签抓下来，再交给模型写用例。

生成后会拿用例里的 role / label / text / testid 与这份摘要逐条比对，摘要里找不到的
定位器会被标记出来（不直接拒绝：摘要只覆盖一个页面状态，登录后的页面往往比登录页大）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .schema import LocatorBy, TestCase

EXTRACT_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const seen = new Set();
  const items = [];
  const labelOf = (el) => {
    if (el.labels && el.labels.length) return clean(el.labels[0].innerText);
    const aria = el.getAttribute('aria-label');
    if (aria) return clean(aria);
    return '';
  };
  const testidOf = (el) =>
    el.getAttribute('data-testid') || el.getAttribute('data-test-id') || '';
  const visible = (el) => (el.checkVisibility ? el.checkVisibility() : el.offsetParent !== null);
  const push = (el) => {
    if (seen.has(el) || items.length >= 120 || !visible(el)) return;
    seen.add(el);
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || '';
    const text = clean(el.innerText || el.value || '');
    const item = {
      tag,
      role,
      name: '',
      label: '',
      placeholder: el.getAttribute('placeholder') || '',
      type: el.getAttribute('type') || '',
      testid: testidOf(el),
    };
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      item.label = labelOf(el);
      item.name = item.label || item.placeholder || item.name;
    } else {
      item.name = clean(el.getAttribute('aria-label')) || text.slice(0, 40);
    }
    if (item.name || item.testid || item.label) items.push(item);
  };
  document.querySelectorAll('input, textarea, select, button, a[href], [role="button"], [data-testid]').forEach(push);

  const headings = Array.from(document.querySelectorAll('h1, h2, h3'))
    .filter(visible)
    .map((h) => clean(h.innerText))
    .filter(Boolean)
    .slice(0, 20);
  const alerts = Array.from(document.querySelectorAll('[role="alert"], .error, .message, .toast'))
    .filter(visible)
    .map((el) => clean(el.innerText))
    .filter(Boolean)
    .slice(0, 20);
  return {
    title: document.title,
    url: location.href,
    headings,
    alerts,
    elements: items,
    text: clean(document.body.innerText).slice(0, 1600),
  };
}
"""


def capture_outline(url: str, timeout_ms: int = 15000, headless: bool = True) -> dict[str, Any]:
    # 延迟导入：只在需要探测页面时才要求安装 Playwright 浏览器。
    from playwright.sync_api import sync_playwright

    from .browser import launch as launch_browser

    with sync_playwright() as p:
        browser = launch_browser(p, headless=headless)
        page = browser.new_page(viewport={"width": 1360, "height": 900})
        try:
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            data = page.evaluate(EXTRACT_JS)
        finally:
            browser.close()
    data["requested_url"] = url
    return data


def format_outline(outline: dict[str, Any], max_elements: int = 60, max_text: int = 900) -> str:
    """把 outline 压成适合放进提示词的紧凑文本。"""
    lines: list[str] = []
    lines.append(f"页面标题：{outline.get('title', '')}")
    lines.append(f"页面地址：{outline.get('url', '')}")
    headings = outline.get("headings") or []
    if headings:
        lines.append("标题结构：" + " | ".join(headings[:8]))
    alerts = outline.get("alerts") or []
    if alerts:
        lines.append("提示文本：" + " | ".join(alerts[:6]))

    elements = outline.get("elements") or []
    lines.append(f"\n可交互元素（共 {len(elements)} 个，最多展示 {max_elements} 个）：")
    for el in elements[:max_elements]:
        parts = [el.get("tag", "?")]
        if el.get("role"):
            parts.append(f"role={el['role']}")
        if el.get("type"):
            parts.append(f"type={el['type']}")
        if el.get("label"):
            parts.append(f'label="{el["label"]}"')
        if el.get("name") and el.get("name") != el.get("label"):
            parts.append(f'name="{el["name"]}"')
        if el.get("placeholder"):
            parts.append(f'placeholder="{el["placeholder"]}"')
        if el.get("testid"):
            parts.append(f'testid="{el["testid"]}"')
        lines.append("  - " + " ".join(parts))

    text = outline.get("text") or ""
    if text:
        lines.append("\n可见文本（截断）：\n" + text[:max_text])
    return "\n".join(lines)


def outline_to_json(outline: dict[str, Any]) -> str:
    return json.dumps(outline, ensure_ascii=False, indent=2)


@dataclass
class LocatorIndex:
    """从 outline 里反查得到的可用定位器集合。"""

    testids: set[str] = field(default_factory=set)
    labels: set[str] = field(default_factory=set)
    placeholders: set[str] = field(default_factory=set)
    names: set[str] = field(default_factory=set)
    role_names: set[str] = field(default_factory=set)
    text_blob: str = ""


def build_locator_index(outline: dict[str, Any]) -> LocatorIndex:
    index = LocatorIndex()
    names: list[str] = []
    for element in outline.get("elements") or []:
        testid = (element.get("testid") or "").strip()
        label = (element.get("label") or "").strip()
        placeholder = (element.get("placeholder") or "").strip()
        name = (element.get("name") or "").strip()
        tag = (element.get("tag") or "").strip()
        role = (element.get("role") or tag).strip()
        if testid:
            index.testids.add(testid)
        if label:
            index.labels.add(label)
        if placeholder:
            index.placeholders.add(placeholder)
        if name:
            index.names.add(name)
            names.append(name)
        if role and name:
            index.role_names.add(f"{role}|{name}")
    headings = [str(item) for item in outline.get("headings") or []]
    alerts = [str(item) for item in outline.get("alerts") or []]
    index.text_blob = " ".join([str(outline.get("text") or ""), *headings, *alerts, *names])
    return index


def check_case_locators(case: TestCase, index: LocatorIndex) -> list[str]:
    """返回「在页面摘要里找不到」的定位器说明。css 无法可靠核对，跳过。"""
    warnings: list[str] = []
    for number, step in enumerate(case.steps, start=1):
        target = step.target
        if target is None:
            continue
        if target.by == LocatorBy.TESTID:
            found = (target.value or "") in index.testids
        elif target.by == LocatorBy.LABEL:
            found = (target.value or "") in index.labels
        elif target.by == LocatorBy.PLACEHOLDER:
            found = (target.value or "") in index.placeholders
        elif target.by == LocatorBy.TEXT:
            found = (target.value or "") in index.text_blob
        elif target.by == LocatorBy.ROLE:
            found = f"{target.role}|{target.name}" in index.role_names or (target.name or "") in index.names
        else:
            continue
        if not found:
            warnings.append(f"{case.id} 第 {number} 步：{target.describe()} 未出现在页面摘要中")
    return warnings
