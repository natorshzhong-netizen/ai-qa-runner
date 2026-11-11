"""三项可回归的评测：schema 有效性、失败归因、真实执行。

    python eval/run_eval.py

任何一项低于阈值都会以退出码 1 结束，便于放进 CI。
"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.demo import apply_query  # noqa: E402
from src.diagnosis import diagnose_signal  # noqa: E402
from src.executor import Executor, ExecutorOptions  # noqa: E402
from src.models import FailureSignal  # noqa: E402
from src.schema import TestCase, load_cases, validate_case  # noqa: E402
from src.server import running_server  # noqa: E402

EVAL_DIR = ROOT / "eval"
RESULTS_DIR = EVAL_DIR / "results"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class _ServerErrorHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(500)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<html><body><h1>500</h1></body></html>")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@contextmanager
def _error500_server() -> Generator[str, None, None]:
    """真实返回 500 的页面：Playwright 不会为此抛异常，用来覆盖真实归因路径。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ServerErrorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def eval_validity() -> dict:
    data = _read_json(EVAL_DIR / "validity_cases.json")
    tp = fp = tn = fn = 0
    mismatches: list[dict] = []
    for item in data["items"]:
        expected = bool(item["expected_valid"])
        case, error = validate_case(item["case"])
        predicted = case is not None and error is None
        if expected and predicted:
            tp += 1
        elif not expected and predicted:
            fp += 1
            mismatches.append({"note": item.get("note"), "expected": "valid", "predicted": "valid"})
        elif expected and not predicted:
            fn += 1
            mismatches.append({"note": item.get("note"), "expected": "valid", "predicted": error})
        else:
            tn += 1
    total = tp + fp + tn + fn
    return {
        "total": total,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "accuracy": (tp + tn) / total if total else 0.0,
        "precision": tp / (tp + fp) if (tp + fp) else 0.0,
        "recall": tp / (tp + fn) if (tp + fn) else 0.0,
        "mismatches": mismatches,
    }


def eval_diagnosis() -> dict:
    data = _read_json(EVAL_DIR / "diagnosis_cases.json")
    correct = 0
    mismatches: list[dict] = []
    per_class: dict[str, dict[str, int]] = {}
    for item in data["items"]:
        signal = FailureSignal.model_validate(item["signal"])
        predicted = diagnose_signal(signal).category.value
        expected = item["expected"]
        bucket = per_class.setdefault(expected, {"correct": 0, "total": 0})
        bucket["total"] += 1
        if predicted == expected:
            correct += 1
            bucket["correct"] += 1
        else:
            mismatches.append({"id": item["id"], "expected": expected, "predicted": predicted})
    total = len(data["items"])
    return {
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else 0.0,
        "per_class": per_class,
        "mismatches": mismatches,
    }


def _case(payload: dict) -> TestCase:
    return TestCase.model_validate(payload)


def _eval_live() -> dict:
    curated, issues, _meta = load_cases(ROOT / "cases" / "demo-shop.json")
    if issues:
        return {"error": f"固定用例本身未通过校验：{issues}", "checks": []}
    by_id = {c.id: c for c in curated}

    bug_cases = []
    for case_id, bugs in (
        ("login-valid-account", "bugs=login"),
        ("cart-add-twice", "bugs=cart"),
        ("login-empty-username", "bugs=validation"),
    ):
        bug_cases.extend(apply_query([by_id[case_id]], bugs))

    locator_case = _case(
        {
            "id": "dx-live-locator",
            "title": "不存在的元素应归因为定位失效",
            "requirement_ref": "eval#locator",
            "steps": [
                {"action": "goto", "url": "/"},
                {
                    "action": "click",
                    "target": {"by": "testid", "value": "does-not-exist"},
                },
                {"action": "expect_visible", "target": {"by": "text", "value": "欢迎"}},
            ],
        }
    )
    env_case = _case(
        {
            "id": "dx-live-environment",
            "title": "不可达地址应归因为环境问题",
            "requirement_ref": "eval#environment",
            "steps": [
                {"action": "goto", "url": "http://127.0.0.1:1/"},
                {"action": "expect_visible", "target": {"by": "text", "value": "欢迎"}},
            ],
        }
    )
    bug_cases.extend([locator_case, env_case])

    output_dir = RESULTS_DIR / "runs"
    with running_server(ROOT / "demo-app") as url, _error500_server() as error_url:
        http500_case = _case(
            {
                "id": "dx-live-http500",
                "title": "500 页面上的元素缺失应归因为应用缺陷",
                "requirement_ref": "eval#http500",
                "steps": [
                    {"action": "goto", "url": error_url + "/"},
                    {
                        "action": "click",
                        "target": {"by": "testid", "value": "does-not-exist"},
                    },
                    {"action": "expect_visible", "target": {"by": "text", "value": "欢迎"}},
                ],
            }
        )
        clean = Executor(ExecutorOptions(base_url=url, timeout_ms=3000, output_dir=output_dir)).run(
            curated, mode="eval-clean", provider="offline:curated"
        )
        injected = Executor(
            ExecutorOptions(
                base_url=url,
                timeout_ms=2000,
                nav_timeout_ms=8000,
                output_dir=output_dir,
            )
        ).run(bug_cases + [http500_case], mode="eval-injected", provider="offline:curated")

    checks = []
    expected_categories = {
        "login-valid-account": "app_defect",
        "cart-add-twice": "app_defect",
        "login-empty-username": "app_defect",
        "dx-live-locator": "locator",
        "dx-live-environment": "environment",
        "dx-live-http500": "app_defect",
    }
    for case in injected.cases:
        expected = expected_categories.get(case.case_id, "unknown")
        actual = case.diagnosis.category.value if case.diagnosis else "none"
        checks.append(
            {
                "case_id": case.case_id,
                "expected": expected,
                "actual": actual,
                "status": case.status.value,
                "correct": actual == expected,
            }
        )
    correct = sum(1 for c in checks if c["correct"])
    return {
        "clean_passed": clean.passed,
        "clean_total": clean.total,
        "clean_pass_rate": clean.pass_rate,
        "clean_issues": issues,
        "injected_correct": correct,
        "injected_total": len(checks),
        "injected_accuracy": correct / len(checks) if checks else 0.0,
        "checks": checks,
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    validity = eval_validity()
    diagnosis = eval_diagnosis()
    live = _eval_live()

    thresholds = {
        "schema 有效性判定": validity["accuracy"] >= 0.9,
        "失败归因规则": diagnosis["accuracy"] >= 0.9,
        "干净套件全部通过": live.get("clean_pass_rate", 0.0) == 1.0,
        "缺陷注入归因正确": live.get("injected_accuracy", 0.0) >= 0.9,
    }

    print("ai-qa-runner · eval")
    print("=" * 64)
    print(
        f"schema 有效性   {validity['accuracy'] * 100:5.1f}%"
        f"   精确 {validity['precision'] * 100:.0f}% / 召回 {validity['recall'] * 100:.0f}%"
        f"   ({validity['total']} 条)"
    )
    print(f"失败归因规则     {diagnosis['accuracy'] * 100:5.1f}%   ({diagnosis['total']} 条)")
    print(f"干净套件         {live.get('clean_passed', 0)}/{live.get('clean_total', 0)} 通过")
    print(f"缺陷注入归因     {live.get('injected_correct', 0)}/{live.get('injected_total', 0)} 正确")
    for check in live.get("checks", []):
        mark = "✓" if check["correct"] else "✗"
        print(f"  {mark} {check['case_id']:<28} 期望 {check['expected']:<12} 实际 {check['actual']}")

    print("-" * 64)
    failed = [name for name, ok in thresholds.items() if not ok]
    for name, ok in thresholds.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if validity["mismatches"]:
        print("有效性误判：", validity["mismatches"][:5])
    if diagnosis["mismatches"]:
        print("归因误判：", diagnosis["mismatches"][:5])

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "validity": validity,
        "diagnosis": diagnosis,
        "live": live,
        "thresholds": thresholds,
        "passed": not failed,
    }
    (RESULTS_DIR / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n结果：{RESULTS_DIR / 'latest.json'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
