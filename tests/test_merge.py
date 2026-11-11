from __future__ import annotations

from src.executor import merge_attempts
from src.models import (
    CaseResult,
    CaseStatus,
    Diagnosis,
    DiagnosisCategory,
)
from src.schema import TestCase as CaseModel


def _case(case_id: str = "case-one") -> CaseModel:
    return CaseModel.model_validate(
        {
            "id": case_id,
            "title": "示例用例",
            "requirement_ref": "r.md#R1",
            "steps": [
                {"action": "goto", "url": "/"},
                {"action": "expect_visible", "target": {"by": "text", "value": "完成"}},
            ],
        }
    )


def _result(case: CaseModel, status: CaseStatus) -> CaseResult:
    diagnosis = None
    if status != CaseStatus.PASSED:
        diagnosis = Diagnosis(
            category=DiagnosisCategory.APP_DEFECT,
            confidence=0.8,
            reason="结果不符",
        )
    return CaseResult(
        case_id=case.id,
        title=case.title,
        priority=case.priority,
        type=case.type,
        requirement_ref=case.requirement_ref,
        status=status,
        diagnosis=diagnosis,
        error_message="boom" if status != CaseStatus.PASSED else None,
    )


def test_all_passes_stay_passed():
    case = _case()
    merged = merge_attempts([[_result(case, CaseStatus.PASSED)] * 3], [case], 3)
    assert merged[0].status == CaseStatus.PASSED
    assert merged[0].pass_count == 3
    assert merged[0].flaky is False
    assert merged[0].diagnosis is None


def test_all_failures_stay_failed_with_rule_diagnosis():
    case = _case()
    merged = merge_attempts([[_result(case, CaseStatus.FAILED)] * 3], [case], 3)
    assert merged[0].status == CaseStatus.FAILED
    assert merged[0].pass_count == 0
    assert merged[0].diagnosis.category == DiagnosisCategory.APP_DEFECT


def test_mixed_results_become_flaky():
    case = _case()
    attempts = [
        _result(case, CaseStatus.PASSED),
        _result(case, CaseStatus.FAILED),
        _result(case, CaseStatus.PASSED),
    ]
    merged = merge_attempts([attempts], [case], 3)
    assert merged[0].status == CaseStatus.FLAKY
    assert merged[0].flaky is True
    assert merged[0].pass_count == 2
    assert merged[0].attempts == 3
    assert merged[0].diagnosis is not None
    assert merged[0].diagnosis.category == DiagnosisCategory.FLAKY


def test_flaky_keeps_failure_evidence():
    case = _case()
    failed = _result(case, CaseStatus.FAILED)
    failed.error_message = "assert failed"
    merged = merge_attempts(
        [[_result(case, CaseStatus.PASSED), failed, _result(case, CaseStatus.PASSED)]],
        [case],
        3,
    )
    assert merged[0].error_message == "assert failed"
