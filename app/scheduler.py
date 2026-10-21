from __future__ import annotations

import logging
import signal
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select, update

from app.config import get_settings
from app.db import SessionLocal
from app.domain.enums import ExecutionStatus, OutboxStatus, TaskStatus
from app.logging_config import configure_logging, task_id_var, trace_id_var
from app.models import DeadLetter, OutboxEvent, Task, TaskEvent, TaskExecution
from app.services.audit import append_audit_event
from app.services.tasks import utcnow
from app.worker.celery_app import celery_app

settings = get_settings()
logger = logging.getLogger(__name__)
running = True


def _stop(*_: object) -> None:
    global running
    running = False


def _dispatch_once() -> int:
    published = 0
    with SessionLocal() as session:
        now = utcnow()
        session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.status == OutboxStatus.PROCESSING,
                OutboxEvent.locked_at < now - timedelta(seconds=60),
            )
            .values(
                status=OutboxStatus.PENDING, locked_at=None, last_error="dispatcher lease expired"
            )
        )
        events = session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.status == OutboxStatus.PENDING,
                OutboxEvent.available_at <= now,
            )
            .order_by(OutboxEvent.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(settings.outbox_batch_size)
        ).all()
        event_ids = [event.id for event in events]
        for event in events:
            event.status = OutboxStatus.PROCESSING
            event.locked_at = now
            event.attempts += 1
        session.commit()

    for event_id in event_ids:
        with SessionLocal() as session:
            claimed_event = session.get(OutboxEvent, event_id)
            if claimed_event is None:
                continue
            try:
                queue = str(claimed_event.payload.get("queue") or "tasks.normal")
                celery_app.send_task(
                    "app.worker.process_task",
                    args=[str(claimed_event.payload["task_id"]), str(claimed_event.id)],
                    queue=queue,
                    task_id=str(claimed_event.id),
                )
                claimed_event.status = OutboxStatus.PUBLISHED
                claimed_event.published_at = utcnow()
                claimed_event.last_error = None
                published += 1
            except Exception as exc:
                if claimed_event.attempts >= 10:
                    # 有上限的重试，避免永久失败的 outbox 事件无限重投。
                    claimed_event.status = OutboxStatus.FAILED
                    claimed_event.last_error = f"超过最大重试次数: {exc}"
                else:
                    delay = min(
                        settings.retry_base_seconds
                        * (2 ** max(claimed_event.attempts - 1, 0)),
                        60,
                    )
                    claimed_event.status = OutboxStatus.PENDING
                    claimed_event.available_at = utcnow() + timedelta(seconds=delay)
                    claimed_event.last_error = str(exc)
            session.commit()
    return published


def _recover_stale_once() -> int:
    recovered = 0
    now = utcnow()
    with SessionLocal() as session:
        tasks = session.scalars(
            select(Task)
            .where(
                Task.status == TaskStatus.RUNNING,
                Task.lease_expires_at < now,
            )
            .with_for_update(skip_locked=True)
            .limit(20)
        ).all()
        for task in tasks:
            execution = session.scalar(
                select(TaskExecution)
                .where(
                    TaskExecution.task_id == task.id,
                    TaskExecution.status == ExecutionStatus.RUNNING,
                )
                .order_by(TaskExecution.started_at.desc())
                .limit(1)
            )
            previous = task.status
            task.status = TaskStatus.RETRYING
            session.add(
                TaskEvent(
                    task_id=task.id,
                    event_type="task.lease_expired",
                    from_status=previous,
                    to_status=TaskStatus.RETRYING,
                    progress=task.progress,
                    message="Worker 租约过期，调度器执行恢复",
                    trace_id=task.trace_id,
                )
            )
            if execution:
                execution.status = ExecutionStatus.RETRYING
                execution.error = "lease expired"
            if task.attempt_count >= task.max_attempts:
                task.status = TaskStatus.DEAD
                task.failure_code = "EXECUTION_LEASE_EXPIRED"
                task.failure_message = "Worker 执行租约过期且重试次数耗尽"
                if execution:
                    execution.status = ExecutionStatus.DEAD
                    execution.finished_at = utcnow()
                dead_letter = session.scalar(
                    select(DeadLetter).where(DeadLetter.task_id == task.id)
                )
                if dead_letter is None:
                    session.add(
                        DeadLetter(
                            task_id=task.id,
                            outbox_event_id=execution.outbox_event_id if execution else None,
                            reason_code="EXECUTION_LEASE_EXPIRED",
                            reason=task.failure_message,
                            payload={"task_id": str(task.id)},
                        )
                    )
                else:
                    dead_letter.status = "open"
                    dead_letter.reason_code = "EXECUTION_LEASE_EXPIRED"
                    dead_letter.reason = task.failure_message
                    dead_letter.payload = {"task_id": str(task.id)}
                    dead_letter.requeued_at = None
                session.add(
                    TaskEvent(
                        task_id=task.id,
                        event_type="task.dead",
                        from_status=TaskStatus.RETRYING,
                        to_status=TaskStatus.DEAD,
                        progress=task.progress,
                        message="执行租约过期且重试次数耗尽，进入死信队列",
                        trace_id=task.trace_id,
                    )
                )
            else:
                event_id = uuid4()
                payload = {
                    "event_id": str(event_id),
                    "task_id": str(task.id),
                    "priority": task.priority.value,
                    "queue": f"tasks.{task.priority.value}",
                }
                session.add(
                    OutboxEvent(
                        id=event_id,
                        aggregate_type="task",
                        aggregate_id=task.id,
                        event_type="task.dispatch",
                        payload=payload,
                    )
                )
                task.lease_expires_at = None
            append_audit_event(
                session,
                actor_id=None,
                action="task.recovered",
                resource_type="task",
                resource_id=str(task.id),
                trace_id=task.trace_id,
                after_value={"status": task.status.value, "attempt": task.attempt_count},
            )
            recovered += 1
        session.commit()
    return recovered


def main() -> None:
    configure_logging()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    last_recovery = 0.0
    while running:
        trace_id_var.set("scheduler")
        try:
            _dispatch_once()
            if time.monotonic() - last_recovery >= 15:
                _recover_stale_once()
                last_recovery = time.monotonic()
        except Exception:
            logger.exception("scheduler cycle failed", extra={"event": "scheduler.failure"})
            task_id_var.set(None)
        time.sleep(settings.outbox_poll_seconds)


if __name__ == "__main__":
    main()
