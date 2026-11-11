"""执行结果的数据模型。报告、评测、CLI 都只读这一层。"""

from __future__ import annotations

import json
from enum import Enum

from pydantic import BaseModel, Field


class StepStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class CaseStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    FLAKY = "flaky"


class DiagnosisCategory(str, Enum):
    APP_DEFECT = "app_defect"
    LOCATOR = "locator"
    CASE_ERROR = "case_error"
    ENVIRONMENT = "environment"
    FLAKY = "flaky"
    UNKNOWN = "unknown"


CATEGORY_LABELS: dict[DiagnosisCategory, str] = {
    DiagnosisCategory.APP_DEFECT: "应用缺陷",
    DiagnosisCategory.LOCATOR: "定位失效 / 用例错误",
    DiagnosisCategory.CASE_ERROR: "用例不合法",
    DiagnosisCategory.ENVIRONMENT: "环境 / 网络",
    DiagnosisCategory.FLAKY: "不稳定（flaky）",
    DiagnosisCategory.UNKNOWN: "未归类",
}


class FailureSignal(BaseModel):
    """从一次失败里提炼出的、足以做归因的事实。"""

    case_id: str
    step_index: int
    action: str
    error_kind: str
    message: str
    page_errors: list[str] = Field(default_factory=list)
    console_errors: list[str] = Field(default_factory=list)
    failed_requests: list[str] = Field(default_factory=list)
    http_status: int | None = None


class Diagnosis(BaseModel):
    category: DiagnosisCategory
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    evidence: list[str] = Field(default_factory=list)
    hint: str | None = None
    source: str = "rule"


class StepResult(BaseModel):
    index: int
    action: str
    description: str
    status: StepStatus
    duration_ms: int = 0
    error: str | None = None


class CaseResult(BaseModel):
    case_id: str
    title: str
    priority: str
    type: str
    requirement_ref: str
    status: CaseStatus
    duration_ms: int = 0
    steps: list[StepResult] = Field(default_factory=list)
    error_kind: str | None = None
    error_message: str | None = None
    screenshot: str | None = None
    trace: str | None = None
    console_errors: list[str] = Field(default_factory=list)
    page_errors: list[str] = Field(default_factory=list)
    failed_requests: list[str] = Field(default_factory=list)
    http_status: int | None = None
    diagnosis: Diagnosis | None = None
    signal: FailureSignal | None = None
    pass_count: int = 0
    attempts: int = 1
    flaky: bool = False

    @property
    def is_failure(self) -> bool:
        return self.status in (CaseStatus.FAILED, CaseStatus.ERROR)


class RunResult(BaseModel):
    run_id: str
    base_url: str
    mode: str = "default"
    provider: str | None = None
    started_at: str
    duration_ms: int
    repeats: int = 1
    cases: list[CaseResult] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.status == CaseStatus.PASSED)

    @property
    def failed(self) -> int:
        return sum(1 for c in self.cases if c.status == CaseStatus.FAILED)

    @property
    def errors(self) -> int:
        return sum(1 for c in self.cases if c.status == CaseStatus.ERROR)

    @property
    def flaky(self) -> int:
        return sum(1 for c in self.cases if c.status == CaseStatus.FLAKY)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def diagnosis_counts(self) -> dict[DiagnosisCategory, int]:
        counts: dict[DiagnosisCategory, int] = {}
        for case in self.cases:
            if case.diagnosis and case.status in (CaseStatus.FAILED, CaseStatus.ERROR, CaseStatus.FLAKY):
                counts[case.diagnosis.category] = counts.get(case.diagnosis.category, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
