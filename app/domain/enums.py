from enum import StrEnum


class Role(StrEnum):
    SUBMITTER = "submitter"
    REVIEWER = "reviewer"
    ADMIN = "admin"


class TaskType(StrEnum):
    PDF = "pdf"
    SVG = "svg"
    GERBER = "gerber"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REVIEW_PENDING = "review_pending"
    CANCELED = "canceled"
    RETRYING = "retrying"
    DEAD = "dead"
    CANCELING = "canceling"


class TaskPriority(StrEnum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class RuleSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"


class ExecutionStatus(StrEnum):
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


TERMINAL_TASK_STATUSES = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELED,
    TaskStatus.DEAD,
}
ACTIVE_TASK_STATUSES = {
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
    TaskStatus.RETRYING,
    TaskStatus.CANCELING,
    TaskStatus.REVIEW_PENDING,
}
