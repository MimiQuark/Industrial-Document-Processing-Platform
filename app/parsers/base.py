from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import RuleSeverity, TaskType


@dataclass(slots=True)
class Finding:
    code: str
    severity: RuleSeverity
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParseResult:
    task_type: TaskType
    parser_version: str
    metrics: dict[str, Any]
    summary: dict[str, Any]
    findings: list[Finding] = field(default_factory=list)
