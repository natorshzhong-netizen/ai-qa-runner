from __future__ import annotations

import json
from pathlib import Path

from src.diagnosis import classify_exception, diagnose_signal
from src.models import DiagnosisCategory, FailureSignal

ROOT = Path(__file__).resolve().parents[1]

FakeTimeout = type("TimeoutError", (Exception,), {})
FakeAssertion = type("AssertionError", (Exception,), {})


def test_eval_diagnosis_dataset_all_correct():
    data = json.loads((ROOT / "eval" / "diagnosis_cases.json").read_text(encoding="utf-8"))
    wrong = []
    for item in data["items"]:
        signal = FailureSignal.model_validate(item["signal"])
        actual = diagnose_signal(signal).category.value
        if actual != item["expected"]:
            wrong.append((item["id"], item["expected"], actual))
    assert wrong == []


def test_classify_timeout_on_action_is_locator_timeout():
    exc = FakeTimeout("Locator.click: Timeout 5000ms exceeded.")
    assert classify_exception(exc, "click") == "locator_timeout"


def test_classify_timeout_on_goto_is_navigation_timeout():
    exc = FakeTimeout("Page.goto: Timeout 15000ms exceeded.")
    assert classify_exception(exc, "goto") == "navigation_timeout"


def test_classify_connection_error_is_navigation_error():
    exc = Exception("net::ERR_CONNECTION_REFUSED at http://127.0.0.1:1/")
    assert classify_exception(exc, "goto") == "navigation_error"


def test_classify_assertion_is_assertion():
    exc = FakeAssertion("Locator expected to be visible")
    assert classify_exception(exc, "expect_visible") == "assertion"


def test_classify_other_error_is_action_error():
    assert classify_exception(ValueError("bad value"), "fill") == "action_error"


def test_rules_handle_console_env_pattern():
    signal = FailureSignal(
        case_id="x",
        step_index=0,
        action="goto",
        error_kind="navigation_timeout",
        message="Page.goto: Timeout 15000ms exceeded.",
        console_errors=["error: net::ERR_INTERNET_DISCONNECTED"],
    )
    diagnosis = diagnose_signal(signal)
    assert diagnosis.category == DiagnosisCategory.ENVIRONMENT
    assert diagnosis.confidence >= 0.9


def test_failed_request_on_assertion_points_to_environment():
    signal = FailureSignal(
        case_id="x",
        step_index=3,
        action="expect_visible",
        error_kind="assertion",
        message="Locator expected to be visible.",
        failed_requests=["POST /api/cart — net::ERR_CONNECTION_RESET"],
    )
    assert diagnose_signal(signal).category == DiagnosisCategory.ENVIRONMENT
