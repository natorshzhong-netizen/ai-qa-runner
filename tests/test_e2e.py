from __future__ import annotations

from pathlib import Path

import pytest

from src.demo import apply_query
from src.executor import Executor, ExecutorOptions
from src.models import CaseStatus, DiagnosisCategory
from src.schema import load_cases
from src.server import running_server

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.browser


def test_clean_demo_suite_passes(tmp_path: Path):
    cases, issues, _ = load_cases(ROOT / "cases" / "demo-shop.json")
    assert issues == []
    with running_server(ROOT / "demo-app") as url:
        run = Executor(ExecutorOptions(base_url=url, timeout_ms=3000, output_dir=tmp_path)).run(
            cases, mode="test-clean"
        )
    failed = [(c.case_id, c.status.value) for c in run.cases if c.status != CaseStatus.PASSED]
    assert failed == []
    assert run.passed == 10


def test_injected_login_bug_is_attributed_to_app(tmp_path: Path):
    cases, issues, _ = load_cases(ROOT / "cases" / "demo-shop.json")
    assert issues == []
    target = next(c for c in cases if c.id == "login-valid-account")
    with running_server(ROOT / "demo-app") as url:
        run = Executor(ExecutorOptions(base_url=url, timeout_ms=2000, output_dir=tmp_path)).run(
            apply_query([target], "bugs=login"), mode="test-bug"
        )
    result = run.cases[0]
    assert result.status == CaseStatus.FAILED
    assert result.diagnosis is not None
    assert result.diagnosis.category == DiagnosisCategory.APP_DEFECT
