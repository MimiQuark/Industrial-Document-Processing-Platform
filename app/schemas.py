from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import (
    ReviewDecision,
    ReviewStatus,
    Role,
    RuleSeverity,
    TaskPriority,
    TaskStatus,
    TaskType,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserRead(ORMModel):
    id: UUID
    email: str
    display_name: str
    role: Role
    is_active: bool
    created_at: datetime


class FileRead(ORMModel):
    id: UUID
    original_name: str
    content_type: str
    extension: str
    sha256: str
    size_bytes: int
    created_at: datetime


class RuleFindingRead(ORMModel):
    id: UUID
    code: str
    severity: RuleSeverity
    message: str
    details: dict[str, Any]
    created_at: datetime


class ReviewCaseRead(ORMModel):
    id: UUID
    task_id: UUID
    status: ReviewStatus
    assignee_id: UUID | None
    claimed_at: datetime | None
    lease_expires_at: datetime | None
    decision: ReviewDecision | None
    comment: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TaskEventRead(ORMModel):
    id: int
    event_type: str
    from_status: TaskStatus | None
    to_status: TaskStatus
    progress: int
    message: str | None
    trace_id: str
    created_at: datetime


class TaskRead(ORMModel):
    id: UUID
    file_id: UUID
    owner_id: UUID
    task_type: TaskType
    priority: TaskPriority
    status: TaskStatus
    progress: int
    attempt_count: int
    max_attempts: int
    trace_id: str
    failure_code: str | None
    failure_message: str | None
    result_summary: dict[str, Any] | None
    rule_version: int
    parser_version: str | None
    created_at: datetime
    updated_at: datetime
    file: FileRead


class TaskDetail(TaskRead):
    events: list[TaskEventRead] = Field(default_factory=list)
    findings: list[RuleFindingRead] = Field(default_factory=list)
    review: ReviewCaseRead | None = None


class TaskCreateResponse(BaseModel):
    task: TaskRead
    deduplicated: bool


class TaskListResponse(BaseModel):
    items: list[TaskRead]
    total: int
    limit: int
    offset: int


class TaskActionResponse(BaseModel):
    task: TaskRead
    message: str


class ReviewListResponse(BaseModel):
    items: list[ReviewCaseRead]
    total: int
    limit: int
    offset: int


class ReviewDecisionRequest(BaseModel):
    decision: ReviewDecision
    comment: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_comment(self):
        if self.decision == ReviewDecision.REJECT and not (self.comment and self.comment.strip()):
            raise ValueError("驳回时必须填写审核意见")
        return self


class RuleRead(ORMModel):
    id: UUID
    code: str
    name: str
    description: str
    file_type: TaskType | None
    severity: RuleSeverity
    enabled: bool
    config: dict[str, Any]
    version: int
    created_by: UUID | None
    created_at: datetime


class RuleCreate(BaseModel):
    code: str = Field(min_length=3, max_length=120, pattern=r"^[A-Z0-9_]+$")
    name: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=4000)
    file_type: TaskType | None = None
    severity: RuleSeverity
    enabled: bool = True
    config: dict[str, Any]


class RulePatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    severity: RuleSeverity | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None


class DeadLetterRead(ORMModel):
    id: UUID
    task_id: UUID
    outbox_event_id: UUID | None
    reason_code: str
    reason: str
    payload: dict[str, Any]
    status: str
    requeued_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AuditEventRead(ORMModel):
    id: int
    actor_id: UUID | None
    action: str
    resource_type: str
    resource_id: str
    before_value: dict[str, Any] | None
    after_value: dict[str, Any] | None
    trace_id: str
    prev_hash: str
    event_hash: str
    created_at: datetime


class AuditListResponse(BaseModel):
    items: list[AuditEventRead]
    total: int
    limit: int
    offset: int


class AuditVerifyResponse(BaseModel):
    valid: bool
    broken_event_id: str | None = None


class ResultResponse(BaseModel):
    task_id: UUID
    status: TaskStatus
    parser_version: str | None
    summary: dict[str, Any] | None
    findings: list[RuleFindingRead]


class HealthResponse(BaseModel):
    status: str
    dependencies: dict[str, str] = {}
