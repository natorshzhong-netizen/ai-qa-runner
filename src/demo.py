"""演示站相关的辅助：把 ?bugs= 注入到用例的 goto 步骤里。"""

from __future__ import annotations

from .schema import Action, Step, TestCase


def apply_query(cases: list[TestCase], query: str) -> list[TestCase]:
    """把 query string 拼到所有相对 goto 上，保留用例之间互不共享上下文的前提。"""
    if not query:
        return cases
    out: list[TestCase] = []
    for case in cases:
        steps: list[Step] = []
        for step in case.steps:
            if step.action == Action.GOTO and step.url and step.url.startswith("/") and "?" not in step.url:
                clone = step.model_copy()
                clone.url = f"{step.url}?{query}"
                steps.append(clone)
            else:
                steps.append(step)
        out.append(case.model_copy(update={"steps": steps}))
    return out
