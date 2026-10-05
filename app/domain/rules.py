from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import RuleSeverity, TaskStatus, TaskType
from app.models import RuleDefinition, Task
from app.parsers.base import Finding, ParseResult


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "truthy":
        return bool(actual)
    if operator == "falsy":
        return not bool(actual)
    if operator == "gt":
        return actual > expected
    if operator == "gte":
        return actual >= expected
    if operator == "lt":
        return actual < expected
    if operator == "lte":
        return actual <= expected
    if operator == "eq":
        return actual == expected
    if operator == "ne":
        return actual != expected
    raise ValueError(f"不支持的规则操作符: {operator}")


def _message(rule: RuleDefinition, actual: Any, expected: Any) -> str:
    operator = rule.config.get("operator")
    metric = rule.config.get("metric")
    return f"规则 {rule.code} 命中：{metric}={actual}，条件 {operator} {expected}"


def active_rules_for_version(
    session: Session, file_type: TaskType, rule_version: int
) -> list[RuleDefinition]:
    candidates = session.scalars(
        select(RuleDefinition)
        .where(
            RuleDefinition.version <= rule_version,
            RuleDefinition.file_type.in_([file_type, None]),
        )
        .order_by(RuleDefinition.code, RuleDefinition.version.desc())
    ).all()
    latest: dict[str, RuleDefinition] = {}
    for rule in candidates:
        latest.setdefault(rule.code, rule)
    return [rule for rule in latest.values() if rule.enabled]


def evaluate_rules(
    session: Session, task: Task, parse_result: ParseResult
) -> list[tuple[RuleDefinition | None, Finding]]:
    evaluated: list[tuple[RuleDefinition | None, Finding]] = []
    existing_codes = {finding.code for finding in parse_result.findings}
    for finding in parse_result.findings:
        evaluated.append((None, finding))

    for rule in active_rules_for_version(session, task.task_type, task.rule_version):
        metric = rule.config.get("metric")
        if metric not in parse_result.metrics:
            continue
        actual = parse_result.metrics[metric]
        expected = rule.config.get("value")
        operator = str(rule.config.get("operator", "truthy"))
        if _compare(actual, operator, expected):
            finding = Finding(
                code=rule.code,
                severity=rule.severity,
                message=_message(rule, actual, expected),
                details={
                    "metric": metric,
                    "actual": actual,
                    "operator": operator,
                    "expected": expected,
                },
            )
            evaluated.append((rule, finding))
            existing_codes.add(rule.code)
    return evaluated


def determine_status(findings: list[Finding]) -> TaskStatus:
    if any(finding.severity == RuleSeverity.CRITICAL for finding in findings):
        return TaskStatus.FAILED
    if any(finding.severity == RuleSeverity.WARNING for finding in findings):
        return TaskStatus.REVIEW_PENDING
    return TaskStatus.SUCCEEDED
