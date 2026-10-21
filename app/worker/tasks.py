from __future__ import annotations

import json
import os
import socket
import tempfile
from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from celery.utils.log import get_task_logger
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.domain.enums import (
    ExecutionStatus,
    ReviewStatus,
    RuleSeverity,
    TaskStatus,
)
from app.domain.errors import PermanentProcessingError, ProcessingError, TransientProcessingError
from app.domain.rules import determine_status, evaluate_rules
from app.logging_config import configure_logging, task_id_var, trace_id_var
from app.models import (
    DeadLetter,
    ParseArtifact,
    ReviewCase,
    RuleFinding,
    Task,
    TaskExecution,
)
from app.parsers import parse_file
from app.services.audit import append_audit_event
from app.services.events import push_dead_letter
from app.services.storage import storage
from app.services.tasks import add_task_event, transition_task, utcnow
from app.worker.celery_app import celery_app

logger = get_task_logger(__name__)
settings = get_settings()


def _lease_expiry():
    return utcnow() + timedelta(seconds=settings.execution_lease_seconds)


def _claim_execution(
    session, task_id: UUID, event_id: UUID, celery_task_id: str
) -> tuple[Task | None, TaskExecution | None]:
    task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        return None, None
    if task.status in {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
        TaskStatus.DEAD,
        TaskStatus.REVIEW_PENDING,
    }:
        return None, None

    execution = session.scalar(
        select(TaskExecution).where(TaskExecution.outbox_event_id == event_id).with_for_update()
    )
    now = utcnow()
    if execution and execution.status in {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.DEAD,
    }:
        return None, None
    if (
        execution
        and execution.status == ExecutionStatus.RUNNING
        and execution.lease_expires_at > now
    ):
        return None, None

    if task.status == TaskStatus.CANCELING:
        transition_task(
            session,
            task,
            target=TaskStatus.CANCELED,
            progress=task.progress,
            trace_id=task.trace_id,
            event_type="task.canceled",
            message="Worker 已确认取消",
        )
        if execution:
            execution.status = ExecutionStatus.FAILED
            execution.error = "canceled"
            execution.finished_at = now
        session.commit()
        return None, None

    task.attempt_count += 1
    task.celery_task_id = celery_task_id
    task.lease_expires_at = _lease_expiry()
    if task.status in {TaskStatus.QUEUED, TaskStatus.RETRYING}:
        transition_task(
            session,
            task,
            target=TaskStatus.RUNNING,
            progress=max(task.progress, 5),
            trace_id=task.trace_id,
            event_type="task.started",
            message=f"开始第 {task.attempt_count} 次处理",
        )
    else:
        task.progress = max(task.progress, 5)
        add_task_event(
            session,
            task,
            event_type="task.recovered",
            from_status=task.status,
            to_status=task.status,
            progress=task.progress,
            trace_id=task.trace_id,
            message="接管过期执行租约",
        )

    if execution is None:
        execution = TaskExecution(
            task_id=task.id,
            outbox_event_id=event_id,
            attempt_no=task.attempt_count,
            status=ExecutionStatus.RUNNING,
            worker_id=f"{socket.gethostname()}:{os.getpid()}",
            lease_expires_at=_lease_expiry(),
        )
        session.add(execution)
    else:
        execution.attempt_no = task.attempt_count
        execution.status = ExecutionStatus.RUNNING
        execution.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        execution.lease_expires_at = _lease_expiry()
        execution.error = None
    session.commit()
    session.refresh(task)
    session.refresh(execution)
    return task, execution


def _update_progress(task_id: UUID, progress: int, message: str) -> bool:
    with SessionLocal() as session:
        task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if task is None:
            return False
        if task.status == TaskStatus.CANCELING:
            session.commit()
            return False
        if task.status in {TaskStatus.CANCELED, TaskStatus.FAILED, TaskStatus.DEAD}:
            session.commit()
            return False
        task.progress = max(task.progress, min(progress, 99))
        task.lease_expires_at = _lease_expiry()
        add_task_event(
            session,
            task,
            event_type="task.progress",
            from_status=task.status,
            to_status=task.status,
            progress=task.progress,
            trace_id=task.trace_id,
            message=message,
        )
        session.commit()
        return True


def _finalize_cancel(session, task: Task, execution: TaskExecution) -> None:
    if task.status != TaskStatus.CANCELED:
        transition_task(
            session,
            task,
            target=TaskStatus.CANCELED,
            progress=task.progress,
            trace_id=task.trace_id,
            event_type="task.canceled",
            message="Worker 在检查点确认取消",
        )
    execution.status = ExecutionStatus.FAILED
    execution.error = "canceled"
    execution.finished_at = utcnow()
    session.commit()


def _dead_letter(session, task: Task, execution: TaskExecution, error: ProcessingError) -> None:
    # 取消请求优先于失败处理：否则 CANCELING 会永远停在中间态。
    if task.status == TaskStatus.CANCELING or task.cancel_requested:
        if task.status in {TaskStatus.RUNNING, TaskStatus.RETRYING,
                           TaskStatus.CANCELING}:
            transition_task(
                session,
                task,
                target=TaskStatus.CANCELED,
                progress=task.progress,
                trace_id=task.trace_id,
                event_type="task.canceled",
                message="任务在失败前已请求取消",
            )
        execution.status = ExecutionStatus.FAILED
        execution.error = "canceled"
        execution.finished_at = utcnow()
        session.commit()
        return

    if task.status == TaskStatus.RUNNING:
        transition_task(
            session,
            task,
            target=TaskStatus.RETRYING,
            progress=task.progress,
            trace_id=task.trace_id,
            event_type="task.retrying",
            message="临时故障，准备重试",
        )
    if task.status == TaskStatus.RETRYING:
        transition_task(
            session,
            task,
            target=TaskStatus.DEAD,
            progress=task.progress,
            trace_id=task.trace_id,
            event_type="task.dead",
            message="重试次数耗尽，进入死信队列",
        )
    task.failure_code = error.code
    task.failure_message = str(error)
    execution.status = ExecutionStatus.DEAD
    execution.error = str(error)
    execution.finished_at = utcnow()
    existing = session.scalar(select(DeadLetter).where(DeadLetter.task_id == task.id))
    payload = {"task_id": str(task.id), "attempt": task.attempt_count}
    if existing is None:
        session.add(
            DeadLetter(
                task_id=task.id,
                outbox_event_id=execution.outbox_event_id,
                reason_code=error.code,
                reason=str(error),
                payload=payload,
            )
        )
    else:
        # 第二次死亡必须重新打开死信，否则管理员 requeue 会被状态检查拒绝。
        existing.outbox_event_id = execution.outbox_event_id
        existing.reason_code = error.code
        existing.reason = str(error)
        existing.payload = payload
        existing.status = "open"
        existing.requeued_at = None
    append_audit_event(
        session,
        actor_id=None,
        action="task.dead",
        resource_type="task",
        resource_id=str(task.id),
        trace_id=task.trace_id,
        after_value={"failure_code": error.code, "attempt": task.attempt_count},
    )
    session.commit()
    push_dead_letter(
        task.id,
        {"reason_code": error.code, "reason": str(error), "attempt": task.attempt_count},
    )


def _finalize_failure(
    session, task: Task, execution: TaskExecution, error: ProcessingError
) -> None:
    transition_task(
        session,
        task,
        target=TaskStatus.FAILED,
        progress=task.progress,
        trace_id=task.trace_id,
        event_type="task.failed",
        message=str(error),
    )
    task.failure_code = error.code
    task.failure_message = str(error)
    session.add(
        RuleFinding(
            task_id=task.id,
            rule_id=None,
            code=error.code,
            severity=RuleSeverity.CRITICAL,
            message=str(error),
            details={},
        )
    )
    execution.status = ExecutionStatus.FAILED
    execution.error = str(error)
    execution.finished_at = utcnow()
    append_audit_event(
        session,
        actor_id=None,
        action="task.failed",
        resource_type="task",
        resource_id=str(task.id),
        trace_id=task.trace_id,
        after_value={"failure_code": error.code},
    )
    session.commit()


def _finalize_success(
    session, task: Task, execution: TaskExecution, parse_result, evaluated
) -> None:
    artifact_payload = {
        "parser_version": parse_result.parser_version,
        "metrics": parse_result.metrics,
        "summary": parse_result.summary,
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity.value,
                "message": finding.message,
                "details": finding.details,
            }
            for _, finding in evaluated
        ],
    }
    payload = json.dumps(artifact_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    object_key = f"{task.id}/result.json"
    stored = storage.put_bytes(
        storage.settings.result_bucket, object_key, payload, "application/json"
    )
    session.add(
        ParseArtifact(
            task_id=task.id,
            kind="result",
            bucket=stored.bucket,
            object_key=stored.object_key,
            content_type="application/json",
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            summary=artifact_payload,
        )
    )
    for rule, finding in evaluated:
        session.add(
            RuleFinding(
                task_id=task.id,
                rule_id=rule.id if rule else None,
                code=finding.code,
                severity=finding.severity,
                message=finding.message,
                details=finding.details,
            )
        )
    target = determine_status([finding for _, finding in evaluated])
    task.result_summary = parse_result.summary
    task.parser_version = parse_result.parser_version
    transition_task(
        session,
        task,
        target=target,
        progress=100,
        trace_id=task.trace_id,
        event_type="task.completed",
        message="解析和规则检查完成",
    )
    if target == TaskStatus.FAILED:
        critical = next(
            finding for _, finding in evaluated if finding.severity == RuleSeverity.CRITICAL
        )
        task.failure_code = critical.code
        task.failure_message = critical.message
    elif target == TaskStatus.REVIEW_PENDING:
        existing = session.scalar(select(ReviewCase).where(ReviewCase.task_id == task.id))
        if existing is None:
            session.add(ReviewCase(task_id=task.id, status=ReviewStatus.PENDING))
    execution.status = ExecutionStatus.SUCCEEDED
    execution.finished_at = utcnow()
    append_audit_event(
        session,
        actor_id=None,
        action="task.processed",
        resource_type="task",
        resource_id=str(task.id),
        trace_id=task.trace_id,
        after_value={"status": target.value, "parser_version": parse_result.parser_version},
    )
    session.commit()


def _cancel_if_requested(task_id: UUID, event_id: UUID) -> bool:
    with SessionLocal() as session:
        task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        execution = session.scalar(
            select(TaskExecution).where(TaskExecution.outbox_event_id == event_id)
        )
        if task and execution and task.status == TaskStatus.CANCELING:
            _finalize_cancel(session, task, execution)
            return True
    return False


@celery_app.task(
    bind=True,
    name="app.worker.process_task",
    acks_late=True,
    max_retries=settings.retry_max_retries,
)
def process_task(self, task_id: str, event_id: str) -> str:
    configure_logging()
    task_uuid = UUID(task_id)
    event_uuid = UUID(event_id)
    task_token = task_id_var.set(task_id)
    trace_token = None
    with SessionLocal() as session:
        task, execution = _claim_execution(session, task_uuid, event_uuid, str(self.request.id))
        if task is None or execution is None:
            task_id_var.reset(task_token)
            return "ignored"
        task_type = task.task_type
        raw_bucket = task.file.bucket
        raw_object_key = task.file.object_key
        trace_token = trace_id_var.set(task.trace_id)

    temp_path: Path | None = None
    try:
        if not _update_progress(task_uuid, 10, "正在下载原始文件"):
            return "canceled" if _cancel_if_requested(task_uuid, event_uuid) else "ignored"

        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{task_type.value}") as temp:
            temp_path = Path(temp.name)
        with temp_path.open("wb") as target:
            storage.download_to(raw_bucket, raw_object_key, target)

        if not _update_progress(task_uuid, 35, "正在进行工业图档解析"):
            return "canceled" if _cancel_if_requested(task_uuid, event_uuid) else "ignored"
        parse_result = parse_file(task_type, temp_path)

        if not _update_progress(task_uuid, 70, "正在执行审核规则"):
            return "canceled" if _cancel_if_requested(task_uuid, event_uuid) else "ignored"

        with SessionLocal() as session:
            task = session.scalar(select(Task).where(Task.id == task_uuid).with_for_update())
            execution = session.scalar(
                select(TaskExecution).where(TaskExecution.outbox_event_id == event_uuid)
            )
            if task is None or execution is None:
                return "ignored"
            if task.status == TaskStatus.CANCELING:
                _finalize_cancel(session, task, execution)
                return "canceled"
            if task.celery_task_id != str(self.request.id):
                execution.status = ExecutionStatus.FAILED
                execution.error = "superseded by recovered execution"
                execution.finished_at = utcnow()
                session.commit()
                return "ignored"
            evaluated = evaluate_rules(session, task, parse_result)
            _finalize_success(session, task, execution, parse_result, evaluated)
        return "succeeded"
    except PermanentProcessingError as exc:
        logger.exception("permanent processing failure", extra={"event": "task.permanent_failure"})
        with SessionLocal() as session:
            task = session.scalar(select(Task).where(Task.id == task_uuid).with_for_update())
            execution = session.scalar(
                select(TaskExecution).where(TaskExecution.outbox_event_id == event_uuid)
            )
            if task and execution:
                if task.celery_task_id != str(self.request.id):
                    execution.status = ExecutionStatus.FAILED
                    execution.error = "superseded by recovered execution"
                    execution.finished_at = utcnow()
                    session.commit()
                    return "ignored"
                _finalize_failure(session, task, execution, exc)
        return "failed"
    except Exception as exc:
        logger.exception("transient processing failure", extra={"event": "task.transient_failure"})
        processing_error = (
            exc if isinstance(exc, ProcessingError) else TransientProcessingError(str(exc))
        )
        with SessionLocal() as session:
            task = session.scalar(select(Task).where(Task.id == task_uuid).with_for_update())
            execution = session.scalar(
                select(TaskExecution).where(TaskExecution.outbox_event_id == event_uuid)
            )
            if task is None or execution is None:
                raise
            if task.celery_task_id != str(self.request.id):
                execution.status = ExecutionStatus.FAILED
                execution.error = "superseded by recovered execution"
                execution.finished_at = utcnow()
                session.commit()
                return "ignored"
            if task.attempt_count < task.max_attempts:
                if task.status == TaskStatus.RUNNING:
                    transition_task(
                        session,
                        task,
                        target=TaskStatus.RETRYING,
                        progress=task.progress,
                        trace_id=task.trace_id,
                        event_type="task.retrying",
                        message=f"临时故障，将自动重试: {exc}",
                    )
                task.failure_code = processing_error.code
                task.failure_message = str(exc)
                execution.status = ExecutionStatus.RETRYING
                execution.error = str(exc)
                execution.lease_expires_at = _lease_expiry()
                session.commit()
                countdown = min(
                    settings.retry_base_seconds * (2 ** max(task.attempt_count - 1, 0)),
                    settings.retry_max_seconds,
                )
                raise self.retry(exc=exc, countdown=countdown) from exc
            _dead_letter(session, task, execution, processing_error)
        return "dead"
    finally:
        if temp_path:
            with suppress(OSError):
                temp_path.unlink()
        if trace_token is not None:
            trace_id_var.reset(trace_token)
        task_id_var.reset(task_token)
