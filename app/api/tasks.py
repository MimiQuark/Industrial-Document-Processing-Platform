from __future__ import annotations

import json
import time
from collections.abc import Generator
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from redis.client import PubSub
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.dependencies import get_current_user, get_trace_id
from app.domain.enums import TaskPriority, TaskStatus, TaskType
from app.models import Task, TaskEvent, User
from app.schemas import (
    ResultResponse,
    RuleFindingRead,
    TaskCreateResponse,
    TaskDetail,
    TaskListResponse,
    TaskRead,
)
from app.services.events import redis_client, task_channel
from app.services.storage import storage
from app.services.tasks import (
    cancel_task,
    create_task,
    ensure_task_access,
    get_artifact,
    get_result,
    get_task_detail,
    get_task_or_404,
    list_tasks,
    retry_task,
)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.post("", response_model=TaskCreateResponse)
def create(
    response: Response,
    file: UploadFile = File(...),
    task_type: TaskType | None = Form(default=None),
    priority: TaskPriority = Form(default=TaskPriority.NORMAL),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id),
):
    task, deduplicated = create_task(
        session,
        owner=user,
        upload=file,
        idempotency_key=idempotency_key,
        priority=priority,
        requested_type=task_type,
        trace_id=trace_id,
    )
    response.status_code = 200 if deduplicated else 201
    return TaskCreateResponse(task=TaskRead.model_validate(task), deduplicated=deduplicated)


@router.get("", response_model=TaskListResponse)
def list_all(
    status: TaskStatus | None = None,
    task_type: TaskType | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    items, total = list_tasks(
        session, user, status=status, task_type=task_type, limit=limit, offset=offset
    )
    return TaskListResponse(
        items=[TaskRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{task_id}", response_model=TaskDetail)
def detail(
    task_id: UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    data = get_task_detail(session, task_id, user)
    base = TaskRead.model_validate(data)
    return TaskDetail(
        **base.model_dump(),
        events=data["events"],
        findings=data["findings"],
        review=data["review"],
    )


@router.post("/{task_id}/cancel", response_model=TaskRead)
def cancel(
    task_id: UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id),
):
    return TaskRead.model_validate(cancel_task(session, task_id, user, trace_id))


@router.post("/{task_id}/retry", response_model=TaskRead)
def retry(
    task_id: UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id),
):
    return TaskRead.model_validate(retry_task(session, task_id, user, trace_id))


@router.get("/{task_id}/result", response_model=ResultResponse)
def result(
    task_id: UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    task, findings = get_result(session, task_id, user)
    return ResultResponse(
        task_id=task.id,
        status=task.status,
        parser_version=task.parser_version,
        summary=task.result_summary,
        findings=[RuleFindingRead.model_validate(item) for item in findings],
    )


@router.get("/{task_id}/download")
def download(
    task_id: UUID,
    kind: str = Query(pattern="^(raw|result)$"),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    task = get_task_or_404(session, task_id)
    ensure_task_access(task, user)
    if kind == "raw":
        bucket = task.file.bucket
        key = task.file.object_key
        filename = task.file.original_name
        content_type = task.file.content_type
    else:
        artifact, _ = get_artifact(session, task_id, "result", user)
        bucket = artifact.bucket
        key = artifact.object_key
        filename = f"task-{task.id}-result.json"
        content_type = artifact.content_type

    def stream() -> Generator[bytes, None, None]:
        response = storage.client.get_object(bucket, key)
        try:
            yield from response.stream(1024 * 1024)
        finally:
            response.close()
            response.release_conn()

    safe_filename = quote(filename)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}"}
    return StreamingResponse(stream(), media_type=content_type, headers=headers)


@router.get("/{task_id}/events")
def events(
    task_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    task = get_task_or_404(session, task_id)
    ensure_task_access(task, user)

    def event_generator() -> Generator[str, None, None]:
        last_id = 0
        pubsub: PubSub = redis_client.pubsub()
        pubsub.subscribe(task_channel(task_id))
        try:
            with SessionLocal() as stream_session:
                initial = stream_session.scalars(
                    select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.id)
                ).all()
            for event in initial:
                last_id = max(last_id, event.id)
                payload = {
                    "id": event.id,
                    "event_type": event.event_type,
                    "from_status": event.from_status.value if event.from_status else None,
                    "to_status": event.to_status.value,
                    "progress": event.progress,
                    "message": event.message,
                    "trace_id": event.trace_id,
                    "created_at": event.created_at.isoformat(),
                }
                sse = (
                    f"id: {event.id}\nevent: snapshot\n"
                    f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                )
                yield sse
            while True:
                message = pubsub.get_message(timeout=10)
                if message and message.get("type") == "message":
                    try:
                        payload = json.loads(str(message["data"]))
                        last_id = max(last_id, int(payload.get("id") or 0))
                        sse = (
                            f"id: {last_id}\nevent: update\n"
                            f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                        )
                        yield sse
                    except (ValueError, TypeError):
                        continue
                else:
                    yield ": heartbeat\n\n"
                    # 任务进入终态后关闭 SSE，避免长期占用线程池连接。
                    with SessionLocal() as check_session:
                        status = check_session.scalar(
                            select(Task.status).where(Task.id == task_id)
                        )
                    if status in {
                        TaskStatus.SUCCEEDED, TaskStatus.FAILED,
                        TaskStatus.CANCELED, TaskStatus.DEAD,
                        TaskStatus.REVIEW_PENDING,
                    }:
                        break
                if request.is_disconnected():
                    break
                time.sleep(0.1)
        finally:
            pubsub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
