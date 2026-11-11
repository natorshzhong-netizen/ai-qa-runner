"""失败归因：先规则，后模型。

规则只依赖执行器给出的确定性事实（异常类型、页面错误、请求失败、HTTP 状态），
所以同一份输入永远得到同一个分类，可以进评测集、可以回归。
模型只在规则给不出高置信结论时补一句根因说明，不改变分类。
"""

from __future__ import annotations

from .models import CATEGORY_LABELS, Diagnosis, DiagnosisCategory, FailureSignal

ENV_PATTERNS = (
    "ERR_NAME_NOT_RESOLVED",
    "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_TIMED_OUT",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_ADDRESS_UNREACHABLE",
    "ERR_SSL",
    "ECONNREFUSED",
    "ENOTFOUND",
    "socket hang up",
)


def classify_exception(exc: BaseException, action: str) -> str:
    """把 Playwright 的异常压成稳定的 error_kind，供归因与统计使用。"""
    name = type(exc).__name__
    message = str(exc)
    is_timeout = "Timeout" in name or "Timeout" in message.split("\n")[0]
    is_assertion = "AssertionError" in name

    if action == "goto":
        if any(p in message for p in ENV_PATTERNS):
            return "navigation_error"
        return "navigation_timeout" if is_timeout else "navigation_error"

    if action.startswith("expect_"):
        if is_assertion:
            return "assertion"
        return "assertion_timeout" if is_timeout else "assertion_error"

    if is_timeout:
        return "locator_timeout"
    return "action_error"


def diagnose_signal(signal: FailureSignal) -> Diagnosis:
    """规则归因。返回的 confidence 表示规则对这条事实的确定程度。"""
    evidence: list[str] = []
    if signal.http_status is not None:
        evidence.append(f"HTTP {signal.http_status}")
    if signal.page_errors:
        evidence.append(f"页面异常 {len(signal.page_errors)} 条")
    if signal.failed_requests:
        evidence.append(f"请求失败 {len(signal.failed_requests)} 条")
    if signal.console_errors:
        evidence.append(f"控制台错误 {len(signal.console_errors)} 条")

    kind = signal.error_kind
    haystack = " ".join([signal.message, *signal.console_errors, *signal.failed_requests])

    if kind.startswith("navigation"):
        if any(p in haystack for p in ENV_PATTERNS):
            return Diagnosis(
                category=DiagnosisCategory.ENVIRONMENT,
                confidence=0.97,
                reason="页面无法连接：域名/端口不可达或连接被重置",
                evidence=evidence,
            )
        if signal.http_status is not None and signal.http_status >= 400:
            return Diagnosis(
                category=DiagnosisCategory.APP_DEFECT,
                confidence=0.9,
                reason=f"页面返回 HTTP {signal.http_status}，服务端响应异常",
                evidence=evidence,
            )
        return Diagnosis(
            category=DiagnosisCategory.ENVIRONMENT,
            confidence=0.8,
            reason="打开页面超时或失败，未取得可测试的页面",
            evidence=evidence,
        )

    # goto 返回 5xx 时 Playwright 不会抛异常，后续步骤是在错误页面上执行的。
    # 不单独判断这一条，会把「服务端 500」误报成定位失效或用例写错。
    if signal.http_status is not None and signal.http_status >= 500:
        return Diagnosis(
            category=DiagnosisCategory.APP_DEFECT,
            confidence=0.88,
            reason=f"页面返回 HTTP {signal.http_status}，后续步骤在错误页面上执行",
            evidence=evidence,
        )

    if kind == "case_error":
        return Diagnosis(
            category=DiagnosisCategory.CASE_ERROR,
            confidence=0.95,
            reason="用例动作本身无法执行（动作/参数/定位器不合法）",
            evidence=evidence,
        )

    if kind == "locator_timeout":
        if signal.page_errors:
            return Diagnosis(
                category=DiagnosisCategory.APP_DEFECT,
                confidence=0.85,
                reason="页面脚本抛错，目标元素未能渲染",
                evidence=evidence,
            )
        return Diagnosis(
            category=DiagnosisCategory.LOCATOR,
            confidence=0.72,
            reason="目标元素未出现或不可操作：可能页面改版，也可能用例写错了定位",
            evidence=evidence,
        )

    if kind.startswith("assertion"):
        if signal.page_errors:
            return Diagnosis(
                category=DiagnosisCategory.APP_DEFECT,
                confidence=0.9,
                reason="操作已执行，但页面脚本异常，断言结果不符",
                evidence=evidence,
            )
        if signal.failed_requests:
            return Diagnosis(
                category=DiagnosisCategory.ENVIRONMENT,
                confidence=0.62,
                reason="操作已执行但关键请求失败，先排查网络/接口再判断是否应用缺陷",
                evidence=evidence,
            )
        if signal.action == "expect_url":
            return Diagnosis(
                category=DiagnosisCategory.APP_DEFECT,
                confidence=0.86,
                reason="操作已执行但页面没有跳转到预期地址",
                evidence=evidence,
            )
        return Diagnosis(
            category=DiagnosisCategory.APP_DEFECT,
            confidence=0.8,
            reason="操作已执行成功，但页面结果与需求不符",
            evidence=evidence,
        )

    if kind == "action_error":
        return Diagnosis(
            category=DiagnosisCategory.CASE_ERROR,
            confidence=0.7,
            reason="动作执行报错且非元素缺失，检查用例参数是否合法",
            evidence=evidence,
        )

    return Diagnosis(
        category=DiagnosisCategory.UNKNOWN,
        confidence=0.3,
        reason="执行器未识别的失败类型，需要人工查看 trace",
        evidence=evidence,
    )


def explain_with_llm(signal: FailureSignal, diagnosis: Diagnosis, chat) -> str | None:
    """给规则归因补一句人话。失败即返回 None，不阻断报告。"""
    prompt = (
        "你是测试失败归因助手。下面是一个已经由规则归类的失败，"
        "请用不超过 60 字说明最可能的根因，并给一条排查建议。不要改变分类。\n\n"
        f"分类：{CATEGORY_LABELS[diagnosis.category]}\n"
        f"用例：{signal.case_id}，第 {signal.step_index + 1} 步 {signal.action}\n"
        f"错误：{signal.message[:600]}\n"
        f"页面异常：{signal.page_errors[:3]}\n"
        f"失败请求：{signal.failed_requests[:3]}\n"
    )
    try:
        text = chat(
            [
                {"role": "system", "content": "只输出一句中文结论，不要 Markdown。"},
                {"role": "user", "content": prompt},
            ],
            json_mode=False,
            temperature=0.1,
        )
    except Exception:  # noqa: BLE001 - 诊断提示是锦上添花，不能拖垮整个运行
        return None
    text = " ".join(text.split()).strip()
    return text[:160] or None
