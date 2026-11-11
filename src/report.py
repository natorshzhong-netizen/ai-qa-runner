"""自包含 HTML 报告：不依赖任何外部 CSS/JS/图片，双击就能看。

配色使用 dataviz 参考调色板：状态色固定且永远配图标+文字，归因分布用单色有序柱，
不靠颜色单独传达任何信息。
"""

from __future__ import annotations

import base64
import html
from datetime import datetime
from pathlib import Path

from .models import (
    CATEGORY_LABELS,
    CaseResult,
    CaseStatus,
    DiagnosisCategory,
    RunResult,
)

TYPE_LABELS = {
    "positive": "正向",
    "negative": "负向",
    "edge": "边界",
    "regression": "回归",
}

STATUS_META = {
    CaseStatus.PASSED: ("通过", "✓", "passed"),
    CaseStatus.FAILED: ("失败", "✗", "failed"),
    CaseStatus.ERROR: ("错误", "!", "error"),
    CaseStatus.FLAKY: ("不稳定", "⚠", "flaky"),
}


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _ms(value: int) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f} s"
    return f"{value} ms"


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


CSS = """
:root {
  color-scheme: light;
  --page: #f9f9f7;
  --surface: #fcfcfb;
  --ink: #0b0b0b;
  --ink-2: #52514e;
  --muted: #898781;
  --grid: #e1e0d9;
  --border: rgba(11, 11, 11, 0.10);
  --accent: #2a78d6;
  --accent-soft: #eaf2fd;
  --good: #0ca30c;
  --good-text: #006300;
  --warning: #fab219;
  --warning-soft: #fff6df;
  --serious: #ec835a;
  --serious-soft: #fdf0ea;
  --critical: #d03b3b;
  --critical-soft: #fdecec;
  --shadow: 0 1px 2px rgba(11, 11, 11, 0.04);
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page: #0d0d0d;
    --surface: #1a1a19;
    --ink: #f0efec;
    --ink-2: #c3c2b7;
    --muted: #898781;
    --grid: #2c2c2a;
    --border: rgba(255, 255, 255, 0.10);
    --accent: #3987e5;
    --accent-soft: #17273a;
    --good-text: #0ca30c;
    --warning-soft: #2b2110;
    --serious-soft: #2a1c16;
    --critical-soft: #2a1515;
    --shadow: none;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d;
  --surface: #1a1a19;
  --ink: #f0efec;
  --ink-2: #c3c2b7;
  --muted: #898781;
  --grid: #2c2c2a;
  --border: rgba(255, 255, 255, 0.10);
  --accent: #3987e5;
  --accent-soft: #17273a;
  --good-text: #0ca30c;
  --warning-soft: #2b2110;
  --serious-soft: #2a1c16;
  --critical-soft: #2a1515;
  --shadow: none;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--page);
  color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 14px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 1080px; margin: 0 auto; padding: 28px 24px 56px; }

.topbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px; margin-bottom: 26px;
}
.brand { display: flex; align-items: baseline; gap: 12px; }
.brand strong { font-size: 18px; letter-spacing: -0.02em; }
.brand span { color: var(--muted); font-size: 12px; }
.theme-btn {
  padding: 7px 12px; border: 1px solid var(--border); border-radius: 999px;
  background: var(--surface); color: var(--ink-2); font: inherit; font-size: 12px; cursor: pointer;
}
.theme-btn:hover { border-color: var(--accent); color: var(--accent); }

h1 { margin: 0 0 6px; font-size: 26px; letter-spacing: -0.025em; }
h2 { margin: 0 0 14px; font-size: 15px; letter-spacing: -0.01em; }
.sub { margin: 0; color: var(--ink-2); }
.meta { display: flex; flex-wrap: wrap; gap: 8px 18px; margin: 14px 0 26px; color: var(--muted); font-size: 12px; }
.meta code { color: var(--ink-2); font-size: 12px; }

.kpis { display: grid; grid-template-columns: 1.45fr repeat(4, 1fr); gap: 12px; margin-bottom: 26px; }
.kpi {
  padding: 16px 18px; border: 1px solid var(--border); border-radius: 14px;
  background: var(--surface); box-shadow: var(--shadow);
}
.kpi-label { margin: 0; color: var(--ink-2); font-size: 12px; }
.kpi-value { margin: 6px 0 0; font-size: 28px; font-weight: 700; line-height: 1.1; letter-spacing: -0.03em; }
.kpi.hero .kpi-value { font-size: 52px; color: var(--good-text); }
.kpi-sub { margin: 6px 0 0; color: var(--muted); font-size: 11.5px; }
.dot { display: inline-block; width: 8px; height: 8px; margin-right: 6px; border-radius: 50%; vertical-align: 1px; }
.dot.good { background: var(--good); }
.dot.warning { background: var(--warning); }
.dot.serious { background: var(--serious); }
.dot.critical { background: var(--critical); }

.panel {
  margin-bottom: 22px; padding: 20px; border: 1px solid var(--border);
  border-radius: 14px; background: var(--surface); box-shadow: var(--shadow);
}
.panel-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.panel-note { color: var(--muted); font-size: 11.5px; }

.bars { display: flex; flex-direction: column; gap: 10px; margin-top: 6px; }
.bar-row { display: grid; grid-template-columns: 160px 1fr 34px; align-items: center; gap: 12px; }
.bar-label { color: var(--ink-2); font-size: 12.5px; text-align: right; }
.bar-track { height: 10px; border-radius: 4px; background: var(--grid); overflow: hidden; }
.bar-fill { height: 10px; border-radius: 0 4px 4px 0; background: var(--accent); }
.bar-value { color: var(--ink-2); font-size: 12.5px; font-variant-numeric: tabular-nums; }
.empty { margin: 4px 0 0; color: var(--muted); font-size: 13px; }

.filters {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin: 0 0 14px; padding: 10px; border: 1px solid var(--border); border-radius: 12px;
  background: var(--surface);
}
.seg { display: inline-flex; padding: 3px; border-radius: 10px; background: var(--page); gap: 2px; }
.seg button {
  padding: 6px 11px; border: 0; border-radius: 8px; background: transparent;
  color: var(--ink-2); font: inherit; font-size: 12px; cursor: pointer;
}
.seg button[aria-pressed="true"] { background: var(--surface); color: var(--ink); box-shadow: var(--shadow); }
input[type="search"] {
  flex: 1; min-width: 180px; padding: 8px 12px; border: 1px solid var(--border);
  border-radius: 9px; background: var(--page); color: var(--ink); font: inherit; font-size: 12.5px;
}
input[type="search"]:focus { outline: 2px solid var(--accent); outline-offset: 1px; }

.case {
  margin-bottom: 10px; border: 1px solid var(--border); border-radius: 14px;
  background: var(--surface); box-shadow: var(--shadow); overflow: hidden;
}
.case[hidden] { display: none; }
.case summary {
  display: flex; align-items: center; gap: 12px; padding: 13px 16px;
  cursor: pointer; list-style: none;
}
.case summary::-webkit-details-marker { display: none; }
.case summary:hover { background: color-mix(in srgb, var(--accent) 4%, transparent); }
.status-icon {
  display: inline-grid; place-items: center; flex: 0 0 auto;
  width: 24px; height: 24px; border-radius: 50%; font-size: 12px; font-weight: 700;
}
.status-icon.passed { background: color-mix(in srgb, var(--good) 15%, transparent); color: var(--good); }
.status-icon.failed { background: var(--critical-soft); color: var(--critical); }
.status-icon.error { background: var(--serious-soft); color: var(--serious); }
.status-icon.flaky { background: var(--warning-soft); color: var(--ink); }
.case-title { flex: 1; min-width: 0; font-weight: 600; font-size: 13.5px; }
.case-title small { display: block; color: var(--muted); font-size: 11px; font-weight: 400; }
.case-right { display: flex; align-items: center; gap: 8px; flex: 0 0 auto; }
.tag {
  padding: 2px 7px; border: 1px solid var(--border); border-radius: 999px;
  color: var(--ink-2); font-size: 10.5px; white-space: nowrap;
}
.tag.dx { border-color: transparent; background: var(--critical-soft); color: var(--ink); }
.tag.dx-flaky { border-color: transparent; background: var(--warning-soft); color: var(--ink); }
.tag.dx-case_error { border-color: transparent; background: var(--serious-soft); color: var(--ink); }
.case-duration { color: var(--muted); font-size: 11.5px; font-variant-numeric: tabular-nums; }

.case-body { padding: 4px 16px 18px 52px; }
.diagnosis {
  margin: 6px 0 14px; padding: 12px 14px; border: 1px solid var(--border);
  border-left: 3px solid var(--critical); border-radius: 10px; background: var(--page);
}
.diagnosis.flaky { border-left-color: var(--warning); }
.diagnosis.case_error { border-left-color: var(--serious); }
.diagnosis.environment { border-left-color: var(--accent); }
.diagnosis p { margin: 4px 0 0; color: var(--ink-2); font-size: 12.5px; }
.diagnosis .hint { color: var(--ink); }
.diagnosis ul { margin: 6px 0 0; padding-left: 18px; color: var(--muted); font-size: 11.5px; }
.conf { color: var(--muted); font-size: 11px; font-weight: 400; }

table.steps { width: 100%; border-collapse: collapse; margin: 4px 0 14px; font-size: 12.5px; }
table.steps th {
  padding: 6px 8px; border-bottom: 1px solid var(--grid); color: var(--muted);
  font-size: 11px; font-weight: 600; text-align: left;
}
table.steps td { padding: 7px 8px; border-bottom: 1px solid var(--grid); vertical-align: top; }
table.steps tr:last-child td { border-bottom: 0; }
.step-status { width: 26px; font-weight: 700; }
.step-status.passed { color: var(--good); }
.step-status.failed { color: var(--critical); }
.step-status.skipped { color: var(--muted); }
.step-dur { width: 64px; color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.step-error { color: var(--critical); font-family: ui-monospace, "Cascadia Code", Consolas, monospace; font-size: 11px; }

pre.error {
  margin: 0 0 14px; padding: 12px 14px; border-radius: 10px; background: var(--critical-soft);
  color: var(--ink); font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 11.5px; white-space: pre-wrap; word-break: break-word;
}
figure { margin: 0 0 12px; }
figure img { display: block; width: 100%; border: 1px solid var(--border); border-radius: 10px; }
figcaption { margin-top: 6px; color: var(--muted); font-size: 11px; }
details.events { margin: 0 0 10px; }
details.events summary { padding: 6px 0; color: var(--ink-2); font-size: 12px; }
details.events ul { margin: 4px 0 0; padding-left: 18px; color: var(--muted); font-size: 11.5px; }
.trace { margin: 0; color: var(--muted); font-size: 11px; }
.muted { color: var(--muted); }
footer { margin-top: 30px; padding-top: 16px; border-top: 1px solid var(--grid); color: var(--muted); font-size: 11.5px; }

@media (max-width: 760px) {
  .kpis { grid-template-columns: 1fr 1fr; }
  .kpi.hero { grid-column: 1 / -1; }
  .bar-row { grid-template-columns: 110px 1fr 30px; }
  .case-body { padding-left: 16px; }
  .case-right .tag:not(.dx) { display: none; }
}
@media print {
  body { background: #fff; }
  .theme-btn, .filters { display: none; }
  .case { break-inside: avoid; }
}
"""

JS = """
(function () {
  var root = document.documentElement;
  var btn = document.getElementById('theme-toggle');
  try {
    var saved = localStorage.getItem('aiqa-theme');
    if (saved) root.dataset.theme = saved;
  } catch (e) {}
  function effectiveDark() {
    if (root.dataset.theme) return root.dataset.theme === 'dark';
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  }
  if (btn) {
    btn.textContent = effectiveDark() ? '切换浅色' : '切换深色';
    btn.addEventListener('click', function () {
      var next = effectiveDark() ? 'light' : 'dark';
      root.dataset.theme = next;
      try { localStorage.setItem('aiqa-theme', next); } catch (e) {}
      btn.textContent = next === 'dark' ? '切换浅色' : '切换深色';
    });
  }

  var cards = Array.prototype.slice.call(document.querySelectorAll('.case'));
  var search = document.getElementById('case-search');
  var buttons = Array.prototype.slice.call(document.querySelectorAll('[data-filter]'));
  var status = 'all';

  function apply() {
    var q = (search && search.value || '').trim().toLowerCase();
    cards.forEach(function (card) {
      var okStatus = status === 'all' || card.dataset.status === status;
      var okText = !q || (card.dataset.search || '').indexOf(q) !== -1;
      card.hidden = !(okStatus && okText);
    });
  }

  buttons.forEach(function (b) {
    b.addEventListener('click', function () {
      status = b.dataset.filter;
      buttons.forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
      apply();
    });
  });
  if (search) search.addEventListener('input', apply);
})();
"""


def _screenshot_data_uri(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    mime = "image/png"
    if path.suffix.lower() in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _diagnosis_html(case: CaseResult) -> str:
    dx = case.diagnosis
    if dx is None:
        return ""
    label = CATEGORY_LABELS.get(dx.category, str(dx.category))
    extra_class = (
        dx.category.value
        if dx.category
        in (
            DiagnosisCategory.FLAKY,
            DiagnosisCategory.CASE_ERROR,
            DiagnosisCategory.ENVIRONMENT,
        )
        else ""
    )
    evidence = "".join(f"<li>{_e(item)}</li>" for item in dx.evidence)
    hint = f'<p class="hint">模型提示：{_e(dx.hint)}</p>' if dx.hint else ""
    source = "规则" if dx.source == "rule" else dx.source
    return f"""
    <div class="diagnosis {extra_class}">
      <strong>{_e(label)}</strong>
      <span class="conf">· 置信度 {int(dx.confidence * 100)}% · {_e(source)}</span>
      <p>{_e(dx.reason)}</p>
      {hint}
      {f"<ul>{evidence}</ul>" if evidence else ""}
    </div>
    """


def _steps_html(case: CaseResult) -> str:
    rows: list[str] = []
    for step in case.steps:
        icon = {"passed": "✓", "failed": "✗", "skipped": "–"}[step.status.value]
        error = f'<div class="step-error">{_e(step.error)}</div>' if step.error else ""
        rows.append(
            f"""<tr>
              <td class="step-status {step.status.value}">{icon}</td>
              <td>{_e(step.description)}{error}</td>
              <td class="step-dur">{_ms(step.duration_ms) if step.duration_ms else "—"}</td>
            </tr>"""
        )
    return f"""<table class="steps">
      <thead><tr><th></th><th>步骤</th><th>耗时</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>"""


def _events_html(case: CaseResult) -> str:
    groups = [
        ("页面异常", case.page_errors),
        ("控制台错误", case.console_errors),
        ("请求失败", case.failed_requests),
    ]
    blocks = []
    for title, items in groups:
        if not items:
            continue
        lis = "".join(f"<li>{_e(item)}</li>" for item in items[:10])
        blocks.append(
            f'<details class="events"><summary>{title}（{len(items)}）</summary><ul>{lis}</ul></details>'
        )
    return "".join(blocks)


def _case_card(case: CaseResult, output_dir: Path) -> str:
    label, icon, status_class = STATUS_META[case.status]
    dx = case.diagnosis
    dx_tag = ""
    if dx is not None:
        dx_label = CATEGORY_LABELS.get(dx.category, str(dx.category))
        dx_class = "dx" if dx.category == DiagnosisCategory.APP_DEFECT else f"dx dx-{dx.category.value}"
        dx_tag = f'<span class="tag {dx_class}">{_e(dx_label)}</span>'

    type_label = TYPE_LABELS.get(case.type, case.type)
    count = f"通过 {case.pass_count}/{case.attempts} 次" if case.attempts > 1 else ""
    search_blob = " ".join(
        str(part) for part in (case.case_id, case.title, case.requirement_ref, label, dx_tag, type_label)
    ).lower()

    shot = ""
    if case.screenshot:
        uri = _screenshot_data_uri(output_dir / case.screenshot)
        if uri:
            shot = f"""<figure>
              <img src="{uri}" alt="失败步骤截图：{_e(case.title)}" loading="lazy" />
              <figcaption>失败时的整页截图</figcaption>
            </figure>"""

    step_has_error = any(step.error for step in case.steps)
    error = (
        f'<pre class="error">{_e(case.error_message)}</pre>'
        if case.error_message and not step_has_error
        else ""
    )
    trace = f'<p class="trace">trace：{_e(case.trace)}</p>' if case.trace else ""
    open_attr = " open" if case.status != CaseStatus.PASSED else ""

    return f"""
    <article class="case" data-status="{status_class}" data-search="{_e(search_blob)}">
      <details{open_attr}>
        <summary>
          <span class="status-icon {status_class}" aria-hidden="true">{icon}</span>
          <span class="case-title">{_e(case.title)}
            <small>{_e(case.case_id)} · {_e(case.requirement_ref)} · {_e(type_label)} {count}</small>
          </span>
          <span class="case-right">
            <span class="tag">{_e(case.priority)}</span>
            {dx_tag}
            <span class="case-duration">{_ms(case.duration_ms)}</span>
          </span>
        </summary>
        <div class="case-body">
          {_diagnosis_html(case)}
          {_steps_html(case)}
          {error}
          {shot}
          {_events_html(case)}
          {trace}
        </div>
      </details>
    </article>
    """


def _kpis(run: RunResult) -> str:
    rate = run.pass_rate
    return f"""
    <section class="kpis" aria-label="运行概览">
      <div class="kpi hero">
        <p class="kpi-label">通过率</p>
        <p class="kpi-value">{_pct(rate)}</p>
        <p class="kpi-sub">{run.passed} / {run.total} 条用例通过</p>
      </div>
      <div class="kpi">
        <p class="kpi-label"><span class="dot good"></span>通过</p>
        <p class="kpi-value">{run.passed}</p>
        <p class="kpi-sub">含多轮稳定通过</p>
      </div>
      <div class="kpi">
        <p class="kpi-label"><span class="dot critical"></span>失败</p>
        <p class="kpi-value">{run.failed + run.errors}</p>
        <p class="kpi-sub">已全部归因</p>
      </div>
      <div class="kpi">
        <p class="kpi-label"><span class="dot warning"></span>不稳定</p>
        <p class="kpi-value">{run.flaky}</p>
        <p class="kpi-sub">多轮结果不一致</p>
      </div>
      <div class="kpi">
        <p class="kpi-label">总耗时</p>
        <p class="kpi-value">{_ms(run.duration_ms)}</p>
        <p class="kpi-sub">{run.repeats} 轮 × {run.total} 条</p>
      </div>
    </section>
    """


def _distribution(run: RunResult) -> str:
    counts = run.diagnosis_counts()
    if not counts:
        return """
        <section class="panel">
          <div class="panel-head"><h2>失败归因</h2><span class="panel-note">按规则分类</span></div>
          <p class="empty">全部通过，没有需要归因的失败。</p>
        </section>
        """
    max_count = max(counts.values())
    rows = []
    for category, count in counts.items():
        label = CATEGORY_LABELS.get(category, category.value)
        width = max(4.0, count / max_count * 100)
        rows.append(
            f"""<div class="bar-row">
              <span class="bar-label">{_e(label)}</span>
              <div class="bar-track" role="img" aria-label="{_e(label)} {count} 条">
                <div class="bar-fill" style="width:{width:.1f}%"></div>
              </div>
              <span class="bar-value">{count}</span>
            </div>"""
        )
    return f"""
    <section class="panel">
      <div class="panel-head"><h2>失败归因</h2><span class="panel-note">按规则分类，共 {sum(counts.values())} 条</span></div>
      <div class="bars">{"".join(rows)}</div>
    </section>
    """


def _filters(run: RunResult) -> str:
    return f"""
    <div class="filters">
      <div class="seg" role="group" aria-label="按状态筛选">
        <button data-filter="all" aria-pressed="true">全部 {run.total}</button>
        <button data-filter="passed" aria-pressed="false">通过 {run.passed}</button>
        <button data-filter="failed" aria-pressed="false">失败 {run.failed + run.errors}</button>
        <button data-filter="flaky" aria-pressed="false">不稳定 {run.flaky}</button>
      </div>
      <input id="case-search" type="search" placeholder="搜索用例标题 / ID / 需求" aria-label="搜索用例" />
    </div>
    """


def render_report(run: RunResult, output_dir: str | Path, title: str = "ai-qa-runner 运行报告") -> str:
    out = Path(output_dir)
    cases = "".join(_case_card(case, out) for case in run.cases)
    mode = f" · 模式 {_e(run.mode)}" if run.mode and run.mode != "default" else ""
    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    started_at = run.started_at.replace("T", " ")[:19]
    provider = f" · 生成 {_e(run.provider)}" if run.provider else ""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{_e(title)} · {_e(run.run_id)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="topbar">
    <div class="brand"><strong>ai-qa-runner</strong><span>运行报告 / {_e(run.run_id)}</span></div>
    <button id="theme-toggle" class="theme-btn" type="button">切换深色</button>
  </header>

  <h1>{_pct(run.pass_rate)} 用例通过</h1>
  <p class="sub">{run.passed} / {run.total} 通过 · {run.failed + run.errors} 失败 · {run.flaky} 不稳定</p>
  <div class="meta">
    <span>被测地址 <code>{_e(run.base_url)}</code></span>
    <span>开始 {_e(started_at)}{mode}{provider}</span>
    <span>耗时 {_ms(run.duration_ms)}</span>
    <span>生成于 {_e(generated_at)}</span>
  </div>

  {_kpis(run)}
  {_distribution(run)}
  {_filters(run)}

  <section aria-label="用例明细">
    {cases if cases else '<p class="empty">没有用例。</p>'}
  </section>

  <footer>由 ai-qa-runner 生成 · 报告为单文件，可直接归档或发给他人</footer>
</div>
<script>{JS}</script>
</body>
</html>
"""


def write_report(run: RunResult, path: str | Path, output_dir: str | Path, title: str | None = None) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_report(run, output_dir, title=title or "ai-qa-runner 运行报告"),
        encoding="utf-8",
    )
    return target
