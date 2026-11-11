"""Playwright 执行器：跑用例、留证据、做规则归因、合并多轮结果。"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import Browser, Page, expect, sync_playwright

from .browser import launch as launch_browser
from .diagnosis import classify_exception, diagnose_signal
from .models import (
    CaseResult,
    CaseStatus,
    Diagnosis,
    DiagnosisCategory,
    FailureSignal,
    RunResult,
    StepResult,
    StepStatus,
)
from .schema import Action, LocatorBy, Step, Target, TestCase

MAX_EVENT_LOG = 20


@dataclass
class ExecutorOptions:
    base_url: str
    headless: bool = True
    timeout_ms: int = 5000
    nav_timeout_ms: int = 15000
    repeats: int = 1
    trace: bool = False
    slow_mo: int = 0
    output_dir: Path = Path("reports")


def _now_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _clean(text: str, limit: int = 1200) -> str:
    return " ".join(text.split())[:limit]


def _append(bucket: list[str], value: str) -> None:
    if len(bucket) < MAX_EVENT_LOG:
        bucket.append(value)


class Executor:
    def __init__(self, options: ExecutorOptions):
        self.options = options
        self.base_url = options.base_url.rstrip("/")
        self.run_id = _now_id()

    def _url(self, raw: str) -> str:
        raw = raw.strip()
        if raw.startswith(("http://", "https://")):
            return raw
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", raw):
            raise ValueError(f"只允许 http/https 或相对路径，收到 {raw!r}")
        return urljoin(self.base_url + "/", raw.lstrip("/"))

    def _locator(self, page: Page, target: Target | None):
        if target is None:
            raise ValueError("该动作缺少 target")
        if target.by == LocatorBy.ROLE:
            return page.get_by_role(target.role, name=target.name, exact=target.exact)  # type: ignore[arg-type]
        if target.by == LocatorBy.LABEL:
            return page.get_by_label(target.value or "", exact=target.exact)
        if target.by == LocatorBy.TEXT:
            return page.get_by_text(target.value or "", exact=target.exact)
        if target.by == LocatorBy.PLACEHOLDER:
            return page.get_by_placeholder(target.value or "", exact=target.exact)
        if target.by == LocatorBy.TESTID:
            return page.get_by_test_id(target.value or "")
        if target.by == LocatorBy.CSS:
            return page.locator(target.value or "")
        raise ValueError(f"未知定位方式: {target.by}")

    def _dispatch(self, page: Page, step: Step) -> int | None:
        if step.action == Action.GOTO:
            response = page.goto(
                self._url(step.url or "/"),
                wait_until="domcontentloaded",
                timeout=self.options.nav_timeout_ms,
            )
            try:
                page.wait_for_load_state("networkidle", timeout=2000)
            except Exception:  # noqa: BLE001 - networkidle 只是锦上添花
                pass
            return response.status if response is not None else None

        locator = self._locator(page, step.target)
        timeout = self.options.timeout_ms

        if step.action == Action.FILL:
            locator.fill(step.value or "", timeout=timeout)
        elif step.action == Action.CLICK:
            locator.click(timeout=timeout)
        elif step.action == Action.PRESS:
            locator.press(step.value or "", timeout=timeout)
        elif step.action == Action.EXPECT_VISIBLE:
            expect(locator).to_be_visible(timeout=timeout)
        elif step.action == Action.EXPECT_HIDDEN:
            expect(locator).to_be_hidden(timeout=timeout)
        elif step.action == Action.EXPECT_TEXT:
            expect(locator).to_contain_text(step.value or "", timeout=timeout)
        elif step.action == Action.EXPECT_VALUE:
            expect(locator).to_have_value(step.value or "", timeout=timeout)
        elif step.action == Action.EXPECT_URL:
            pattern = re.compile(".*" + re.escape(step.value or "") + ".*")
            expect(page).to_have_url(pattern, timeout=timeout)
        else:
            raise ValueError(f"执行器不支持动作: {step.action}")
        return None

    def _run_case(self, browser: Browser, case: TestCase, rep: int) -> CaseResult:
        run_dir = self.options.output_dir / "runs" / self.run_id
        shot_dir = run_dir / "shots"
        trace_dir = run_dir / "traces"
        started = time.perf_counter()
        console_errors: list[str] = []
        page_errors: list[str] = []
        failed_requests: list[str] = []
        http_status: int | None = None
        steps: list[StepResult] = []
        failure: tuple[int, Step, BaseException, str] | None = None
        trace_path: Path | None = None

        context = browser.new_context(
            viewport={"width": 1360, "height": 900},
            base_url=self.base_url + "/",
        )
        context.set_default_timeout(self.options.timeout_ms)
        page = context.new_page()
        page.on(
            "console",
            lambda msg: _append(console_errors, f"{msg.type}: {msg.text}") if msg.type == "error" else None,
        )
        page.on("pageerror", lambda exc: _append(page_errors, _clean(str(exc), 500)))
        page.on(
            "requestfailed",
            lambda req: (
                _append(failed_requests, f"{req.method} {req.url} — {req.failure}")
                if "favicon" not in req.url.lower()
                else None
            ),
        )

        if self.options.trace:
            trace_dir.mkdir(parents=True, exist_ok=True)
            trace_path = trace_dir / f"{case.id}-r{rep + 1}.zip"
            context.tracing.start(screenshots=True, snapshots=True, sources=False)

        try:
            for index, step in enumerate(case.steps):
                if failure is not None:
                    steps.append(
                        StepResult(
                            index=index,
                            action=step.action.value,
                            description=step.describe(),
                            status=StepStatus.SKIPPED,
                        )
                    )
                    continue
                step_start = time.perf_counter()
                try:
                    status = self._dispatch(page, step)
                    if step.action == Action.GOTO and status is not None:
                        http_status = status
                    steps.append(
                        StepResult(
                            index=index,
                            action=step.action.value,
                            description=step.describe(),
                            status=StepStatus.PASSED,
                            duration_ms=int((time.perf_counter() - step_start) * 1000),
                        )
                    )
                except BaseException as exc:  # noqa: BLE001 - 要记录所有失败再归因
                    kind = classify_exception(exc, step.action.value)
                    failure = (index, step, exc, kind)
                    steps.append(
                        StepResult(
                            index=index,
                            action=step.action.value,
                            description=step.describe(),
                            status=StepStatus.FAILED,
                            duration_ms=int((time.perf_counter() - step_start) * 1000),
                            error=_clean(str(exc), 900),
                        )
                    )
        finally:
            if self.options.trace and trace_path is not None:
                try:
                    context.tracing.stop(path=str(trace_path))
                except Exception:  # noqa: BLE001
                    trace_path = None

        screenshot: Path | None = None
        if failure is not None:
            shot_dir.mkdir(parents=True, exist_ok=True)
            screenshot = shot_dir / f"{case.id}-r{rep + 1}.png"
            try:
                page.screenshot(path=str(screenshot), full_page=True)
            except Exception:  # noqa: BLE001 - 页面可能已经崩了
                screenshot = None

        context.close()
        duration_ms = int((time.perf_counter() - started) * 1000)

        signal: FailureSignal | None = None
        diagnosis: Diagnosis | None = None
        error_kind: str | None = None
        error_message: str | None = None
        if failure is not None:
            index, step, exc, kind = failure
            error_kind = kind
            error_message = _clean(str(exc), 900)
            signal = FailureSignal(
                case_id=case.id,
                step_index=index,
                action=step.action.value,
                error_kind=kind,
                message=error_message,
                page_errors=list(page_errors),
                console_errors=list(console_errors),
                failed_requests=list(failed_requests),
                http_status=http_status,
            )
            diagnosis = diagnose_signal(signal)

        status = CaseStatus.FAILED if failure is not None else CaseStatus.PASSED
        if error_kind == "internal":
            status = CaseStatus.ERROR

        return CaseResult(
            case_id=case.id,
            title=case.title,
            priority=case.priority,
            type=case.type,
            requirement_ref=case.requirement_ref,
            status=status,
            duration_ms=duration_ms,
            steps=steps,
            error_kind=error_kind,
            error_message=error_message,
            screenshot=screenshot.relative_to(self.options.output_dir).as_posix() if screenshot else None,
            trace=trace_path.relative_to(self.options.output_dir).as_posix() if trace_path else None,
            console_errors=console_errors,
            page_errors=page_errors,
            failed_requests=failed_requests,
            http_status=http_status,
            diagnosis=diagnosis,
            signal=signal,
        )

    def run(
        self,
        cases: list[TestCase],
        *,
        mode: str = "default",
        provider: str | None = None,
        explain: Callable[[FailureSignal, Diagnosis], str | None] | None = None,
    ) -> RunResult:
        repeats = max(1, self.options.repeats)
        started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        started = time.perf_counter()
        attempts: list[list[CaseResult]] = [[] for _ in cases]

        with sync_playwright() as p:
            browser = launch_browser(
                p,
                headless=self.options.headless,
                slow_mo=self.options.slow_mo or 0,
            )
            try:
                for rep in range(repeats):
                    for index, case in enumerate(cases):
                        result = self._run_case(browser, case, rep)
                        attempts[index].append(result)
            finally:
                browser.close()

        merged = merge_attempts(attempts, cases, repeats)
        if explain is not None:
            for case in merged:
                if case.signal and case.diagnosis:
                    hint = explain(case.signal, case.diagnosis)
                    if hint:
                        case.diagnosis.hint = hint

        return RunResult(
            run_id=self.run_id,
            base_url=self.base_url,
            mode=mode,
            provider=provider,
            started_at=started_at,
            duration_ms=int((time.perf_counter() - started) * 1000),
            repeats=repeats,
            cases=merged,
        )


def merge_attempts(attempts: list[list[CaseResult]], cases: list[TestCase], repeats: int) -> list[CaseResult]:
    """把同一用例的 N 次结果合并：全过=passed，全挂=failed，有输有赢=flaky。"""
    merged: list[CaseResult] = []
    for index, case in enumerate(cases):
        results = attempts[index]
        pass_count = sum(1 for r in results if r.status == CaseStatus.PASSED)
        first_failed = next((r for r in results if r.status != CaseStatus.PASSED), None)

        if pass_count == repeats and results:
            chosen = results[0].model_copy(deep=True)
            chosen.status = CaseStatus.PASSED
            chosen.diagnosis = None
            chosen.signal = None
            chosen.error_kind = None
            chosen.error_message = None
            chosen.flaky = False
        elif pass_count == 0 and first_failed is not None:
            chosen = first_failed.model_copy(deep=True)
            chosen.status = first_failed.status
            chosen.flaky = False
        else:
            chosen = (first_failed or results[0]).model_copy(deep=True)
            chosen.status = CaseStatus.FLAKY
            chosen.flaky = True
            failed_steps = [s.index + 1 for s in chosen.steps if s.status == StepStatus.FAILED]
            chosen.diagnosis = Diagnosis(
                category=DiagnosisCategory.FLAKY,
                confidence=0.95,
                reason=f"{repeats} 次运行中通过 {pass_count} 次，结果不稳定",
                evidence=[f"通过 {pass_count}/{repeats}", f"首次失败步骤 {failed_steps or '未知'}"],
                source="rule",
            )
            if chosen.signal is not None:
                chosen.signal.error_kind = "flaky"

        chosen.case_id = case.id
        chosen.title = case.title
        chosen.priority = case.priority
        chosen.type = case.type
        chosen.requirement_ref = case.requirement_ref
        chosen.pass_count = pass_count
        chosen.attempts = repeats
        merged.append(chosen)
    return merged


def save_run(run: RunResult, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(run.to_json(), encoding="utf-8")
