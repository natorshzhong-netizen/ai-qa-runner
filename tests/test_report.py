from __future__ import annotations

import base64
from pathlib import Path

from src.models import (
    CaseResult,
    CaseStatus,
    Diagnosis,
    DiagnosisCategory,
    RunResult,
    StepResult,
    StepStatus,
)
from src.report import render_report, write_report

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _case(case_id: str, status: CaseStatus, title: str = "示例用例") -> CaseResult:
    failed = status != CaseStatus.PASSED
    return CaseResult(
        case_id=case_id,
        title=title,
        priority="P0",
        type="positive",
        requirement_ref="r.md#R1",
        status=status,
        duration_ms=812,
        steps=[
            StepResult(
                index=0,
                action="goto",
                description="打开 /",
                status=StepStatus.PASSED,
                duration_ms=310,
            ),
            StepResult(
                index=1,
                action="expect_text",
                description="合计金额包含 598",
                status=StepStatus.FAILED if failed else StepStatus.PASSED,
                duration_ms=502,
                error="Locator expected to contain text '598'" if failed else None,
            ),
        ],
        error_kind="assertion" if failed else None,
        error_message="Locator expected to contain text '598'. Actual: '299'" if failed else None,
        screenshot="shot.png" if failed else None,
        diagnosis=Diagnosis(
            category=DiagnosisCategory.APP_DEFECT,
            confidence=0.8,
            reason="操作已执行成功，但页面结果与需求不符",
            evidence=["HTTP 200"],
        )
        if failed
        else None,
    )


def _run(cases: list[CaseResult]) -> RunResult:
    return RunResult(
        run_id="20260101-000000",
        base_url="http://127.0.0.1:8000",
        started_at="2026-01-01T00:00:00+08:00",
        duration_ms=1200,
        cases=cases,
    )


def test_report_is_self_contained_and_embeds_screenshot(tmp_path: Path):
    (tmp_path / "shot.png").write_bytes(PNG_1X1)
    html = render_report(_run([_case("case-fail", CaseStatus.FAILED)]), tmp_path)
    assert "data:image/png;base64," in html
    assert "应用缺陷" in html
    assert "操作已执行成功" in html
    assert "<style>" in html and "<script>" in html
    assert "http://" not in html.split("<style>")[0]  # 不依赖外部样式


def test_report_escapes_user_content(tmp_path: Path):
    evil = '<img src=x onerror=alert("xss")>'
    html = render_report(_run([_case("case-xss", CaseStatus.FAILED, title=evil)]), tmp_path)
    assert evil not in html
    assert "&lt;img src=x onerror=alert" in html


def test_all_pass_report_has_empty_state():
    html = render_report(_run([_case("case-ok", CaseStatus.PASSED)]), Path("."))
    assert "全部通过，没有需要归因的失败" in html
    assert "100%" in html


def test_run_id_is_escaped_in_title():
    run = _run([_case("case-ok", CaseStatus.PASSED)])
    run.run_id = '<script>alert("run")</script>'
    html = render_report(run, Path("."))
    assert '<script>alert("run")</script>' not in html
    assert "&lt;script&gt;alert" in html


def test_write_report_creates_file(tmp_path: Path):
    target = write_report(_run([_case("case-ok", CaseStatus.PASSED)]), tmp_path / "out" / "r.html", tmp_path)
    assert target.exists()
    assert target.read_text(encoding="utf-8").startswith("<!doctype html>")
