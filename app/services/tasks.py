from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.domain.enums import (
    ACTIVE_TASK_STATUSES,
    Role,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from app.domain.errors import FileValidationError
from app.domain.state_machine import ensure_transition
from app.errors import AppError
from app.models import (
    DeadLetter,
    FileObject,
    OutboxEvent,
    ParseArtifact,
    RuleFinding,
    SystemState,
    Task,
    TaskEvent,
    User,
)
from app.parsers import infer_task_type, validate_signature
from app.services.audit import append_audit_event
from app.services.events import publish_task_event
from app.services.storage import storage

PRIORITY_QUEUES = {
    TaskPriority.HIGH: "tasks.high",
    TaskPriority.NORMAL: "tasks.normal",
    TaskPriority.LOW: "tasks.low",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _event_payload(task: Task, event_id: UUID) -> dict:
    return {
        "event_id": str(event_id),
        "task_id": str(task.id),
        "priority": task.priority.value,
        "queue": PRIORITY_QUEUES[task.priority],
    }


def add_task_event(
    session: Session,
    task: Task,
    *,
    event_type: str,
    from_status: TaskStatus | None,
    to_status: TaskStatus,
    progress: int,
    trace_id: str,
    message: str | None = None,
) -> TaskEvent:
    event = TaskEvent(
        task_id=task.id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        progress=progress,
        message=message,
        trace_id=trace_id,
    )
    session.add(event)
    session.flush()
    publish_task_event(
        task.id,
        {
            "id": event.id,
            "event_type": event_type,
            "from_status": from_status.value if from_status else None,
            "to_status": to_status.value,
            "progress": progress,
            "message": message,
            "trace_id": trace_id,
        },
    )
    return event


def transition_task(
    session: Session,
    task: Task,
    *,
    target: TaskStatus,
    progress: int,
    trace_id: str,
    event_type: str = "status_changed",
    message: str | None = None,
) -> None:
    previous = task.status
    ensure_transition(previous, target)
    task.status = target
    task.progress = max(task.progress, min(progress, 100))
    task.version += 1
    add_task_event(
        session,
        task,
        event_type=event_type,
        from_status=previous,
        to_status=target,
        progress=task.progress,
        trace_id=trace_id,
        message=message,
    )


def _read_upload(upload: UploadFile) -> tuple[Path, str, int, bytes]:
    settings = get_settings()
    digest = hashlib.sha256()
    size = 0
    first_bytes = b""
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=Path(upload.filename or "").suffix
    ) as temp:
        temp_path = Path(temp.name)
        try:
            while chunk := upload.file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise AppError("UPLOAD_TOO_LARGE", "文件超过 50 MiB 限制", 413)
                if len(first_bytes) < 4096:
                    first_bytes += chunk[: 4096 - len(first_bytes)]
                digest.update(chunk)
                temp.write(chunk)
            temp.flush()
        except Exception:
            with suppress(OSError):
                os.unlink(temp_path)
            raise
    return temp_path, digest.hexdigest(), size, first_bytes


def _create_outbox(session: Session, task: Task) -> OutboxEvent:
    event_id = uuid4()
    event = OutboxEvent(
        id=event_id,
        aggregate_type="task",
        aggregate_id=task.id,
        event_type="task.dispatch",
        payload=_event_payload(task, event_id),
    )
    session.add(event)
    session.flush()
    return event


def _rule_version(session: Session) -> int:
    state = session.get(SystemState, "global")
    return state.rule_version if state else 1


def create_task(
    session: Session,
    *,
    owner: User,
    upload: UploadFile,
    idempotency_key: str,
    priority: TaskPriority,
    requested_type: TaskType | None,
    trace_id: str,
) -> tuple[Task, bool]:
    if not 8 <= len(idempotency_key) <= 128:
        raise AppError("INVALID_IDEMPOTENCY_KEY", "Idempotency-Key 长度必须为 8-128 字符", 400)
    if not upload.filename:
        raise AppError("MISSING_FILENAME", "上传文件缺少文件名", 400)

    temp_path, sha256, size, preamble = _read_upload(upload)
    try:
        inferred_type = infer_task_type(upload.filename)
        task_type = requested_type or inferred_type
        if task_type != inferred_type:
            raise FileValidationError("请求的任务类型与文件扩展名不匹配")
        validate_signature(task_type, preamble)

        active_count = session.scalar(
            select(func.count(Task.id)).where(
                Task.owner_id == owner.id, Task.status.in_(ACTIVE_TASK_STATUSES)
            )
        )
        if (active_count or 0) >= get_settings().active_task_limit:
            raise AppError("ACTIVE_TASK_LIMIT", "活动任务数量已达上限", 429)

        existing_by_key = session.scalar(
            select(Task)
            .options(selectinload(Task.file))
            .where(Task.owner_id == owner.id, Task.idempotency_key == idempotency_key)
        )
        if existing_by_key:
            if existing_by_key.file.sha256 != sha256:
                raise AppError(
                    "IDEMPOTENCY_CONFLICT",
                    "相同 Idempotency-Key 已用于不同文件",
                    409,
                    {"task_id": str(existing_by_key.id)},
                )
            return existing_by_key, True

        existing_by_hash = session.scalar(
            select(Task)
            .join(FileObject, Task.file_id == FileObject.id)
            .options(selectinload(Task.file))
            .where(FileObject.owner_id == owner.id, FileObject.sha256 == sha256)
            .order_by(Task.created_at.desc())
        )
        if existing_by_hash:
            return existing_by_hash, True

        extension = Path(upload.filename).suffix.lower()
        object_key = f"{owner.id}/{sha256}/original{extension}"
        with temp_path.open("rb") as stream:
            storage.put_stream(
                storage.settings.raw_bucket,
                object_key,
                stream,
                size,
                upload.content_type or "application/octet-stream",
            )

        file_object = FileObject(
            owner_id=owner.id,
            original_name=Path(upload.filename).name,
            content_type=upload.content_type or "application/octet-stream",
            extension=extension,
            sha256=sha256,
            size_bytes=size,
            bucket=storage.settings.raw_bucket,
            object_key=object_key,
        )
        task = Task(
            file=file_object,
            owner_id=owner.id,
            idempotency_key=idempotency_key,
            task_type=task_type,
            priority=priority,
            status=TaskStatus.QUEUED,
            progress=0,
            max_attempts=get_settings().retry_max_retries + 1,
            trace_id=trace_id,
            rule_version=_rule_version(session),
        )
        session.add_all([file_object, task])
        session.flush()
        _create_outbox(session, task)
        add_task_event(
            session,
            task,
            event_type="task.created",
            from_status=None,
            to_status=TaskStatus.QUEUED,
            progress=0,
            trace_id=trace_id,
            message="任务已创建，等待调度",
        )
        append_audit_event(
            session,
            actor_id=owner.id,
            action="task.created",
            resource_type="task",
            resource_id=str(task.id),
            trace_id=trace_id,
            after_value={
                "status": task.status.value,
                "file_sha256": sha256,
                "task_type": task.task_type.value,
            },
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(
                select(Task)
                .join(FileObject, Task.file_id == FileObject.id)
                .options(selectinload(Task.file))
                .where(
                    FileObject.owner_id == owner.id,
                    FileObject.sha256 == sha256,
                )
                .order_by(Task.created_at.desc())
            )
            if existing is None:
                raise
            return existing, True
        session.refresh(task)
        return task, False
    finally:
        with suppress(OSError):
            os.unlink(temp_path)


def get_task_or_404(session: Session, task_id: UUID) -> Task:
    task = session.scalar(
        select(Task)
        .options(selectinload(Task.file), selectinload(Task.review_case))
        .where(Task.id == task_id)
    )
    if task is None:
        raise AppError("TASK_NOT_FOUND", "任务不存在", 404)
    return task


def ensure_task_access(task: Task, user: User) -> None:
    if user.role == Role.SUBMITTER and task.owner_id != user.id:
        raise AppError("FORBIDDEN", "无权访问该任务", 403)


def list_tasks(
    session: Session,
    user: User,
    *,
    status: TaskStatus | None = None,
    task_type: TaskType | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Task], int]:
    filters = []
    if user.role == Role.SUBMITTER:
        filters.append(Task.owner_id == user.id)
    if status:
        filters.append(Task.status == status)
    if task_type:
        filters.append(Task.task_type == task_type)
    query = select(Task).options(selectinload(Task.file)).where(*filters)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = session.scalars(
        query.order_by(Task.created_at.desc()).limit(min(limit, 100)).offset(max(offset, 0))
    ).all()
    return list(items), total


def get_task_detail(session: Session, task_id: UUID, user: User) -> dict:
    task = get_task_or_404(session, task_id)
    ensure_task_access(task, user)
    events = session.scalars(
        select(TaskEvent)
        .where(TaskEvent.task_id == task.id)
        .order_by(TaskEvent.id.desc())
        .limit(100)
    ).all()
    findings = session.scalars(
        select(RuleFinding)
        .where(RuleFinding.task_id == task.id)
        .order_by(RuleFinding.created_at.desc())
    ).all()
    return {
        **task.__dict__,
        "file": task.file,
        "events": list(reversed(events)),
        "findings": list(findings),
        "review": task.review_case,
    }


def cancel_task(session: Session, task_id: UUID, user: User, trace_id: str) -> Task:
    task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise AppError("TASK_NOT_FOUND", "任务不存在", 404)
    ensure_task_access(task, user)
    if task.status in {TaskStatus.CANCELED, TaskStatus.CANCELING}:
        return task
    if task.status in {TaskStatus.QUEUED, TaskStatus.RETRYING}:
        transition_task(
            session,
            task,
            target=TaskStatus.CANCELED,
            progress=task.progress,
            trace_id=trace_id,
            event_type="task.canceled",
            message="任务已取消",
        )
    elif task.status == TaskStatus.RUNNING:
        task.cancel_requested = True
        transition_task(
            session,
            task,
            target=TaskStatus.CANCELING,
            progress=task.progress,
            trace_id=trace_id,
            event_type="task.canceling",
            message="已请求取消，等待 Worker 检查点",
        )
    else:
        raise AppError("INVALID_STATE_TRANSITION", "当前状态不允许取消", 409)
    append_audit_event(
        session,
        actor_id=user.id,
        action="task.canceled" if task.status == TaskStatus.CANCELED else "task.cancel_requested",
        resource_type="task",
        resource_id=str(task.id),
        trace_id=trace_id,
        after_value={"status": task.status.value},
    )
    session.commit()
    session.refresh(task)
    return task


def retry_task(session: Session, task_id: UUID, user: User, trace_id: str) -> Task:
    task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise AppError("TASK_NOT_FOUND", "任务不存在", 404)
    ensure_task_access(task, user)
    if task.failure_code == "REVIEW_REJECTED":
        raise AppError("REVIEW_REJECTED", "审核驳回任务必须重新提交新文件", 409)
    if task.status not in {TaskStatus.FAILED, TaskStatus.DEAD}:
        raise AppError("INVALID_STATE_TRANSITION", "只有技术失败或死信任务可以重试", 409)
    previous = task.status
    transition_task(
        session,
        task,
        target=TaskStatus.QUEUED,
        progress=0,
        trace_id=trace_id,
        event_type="task.retried",
        message="管理员已手动重试任务",
    )
    task.attempt_count = 0
    task.failure_code = None
    task.failure_message = None
    task.cancel_requested = False
    _create_outbox(session, task)
    dead_letter = session.scalar(select(DeadLetter).where(DeadLetter.task_id == task.id))
    if dead_letter:
        dead_letter.status = "requeued"
        dead_letter.requeued_at = utcnow()
    append_audit_event(
        session,
        actor_id=user.id,
        action="task.retried",
        resource_type="task",
        resource_id=str(task.id),
        trace_id=trace_id,
        before_value={"status": previous.value},
        after_value={"status": task.status.value},
    )
    session.commit()
    session.refresh(task)
    return task


def get_result(session: Session, task_id: UUID, user: User) -> tuple[Task, list[RuleFinding]]:
    task = get_task_or_404(session, task_id)
    ensure_task_access(task, user)
    findings = list(
        session.scalars(select(RuleFinding).where(RuleFinding.task_id == task.id)).all()
    )
    return task, findings


def get_artifact(
    session: Session, task_id: UUID, kind: str, user: User
) -> tuple[ParseArtifact, Task]:
    task = get_task_or_404(session, task_id)
    ensure_task_access(task, user)
    artifact = session.scalar(
        select(ParseArtifact)
        .where(ParseArtifact.task_id == task.id, ParseArtifact.kind == kind)
        .order_by(ParseArtifact.created_at.desc())
    )
    if artifact is None:
        raise AppError("ARTIFACT_NOT_FOUND", "结果文件不存在", 404)
    return artifact, task
