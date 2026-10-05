from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import get_db
from app.domain.enums import OutboxStatus, ReviewStatus
from app.metrics import (
    DEAD_LETTERS,
    OUTBOX_PENDING,
    QUEUE_DEPTH,
    REVIEW_PENDING,
    TASK_PROGRESS,
    TASK_STATE,
)
from app.models import DeadLetter, OutboxEvent, ReviewCase, Task
from app.schemas import HealthResponse
from app.services.events import redis_client
from app.services.storage import storage

router = APIRouter(tags=["observability"])


@router.get("/health/live", response_model=HealthResponse)
def live():
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse)
def ready(session: Session = Depends(get_db)):
    dependencies = {"database": "ok", "redis": "ok", "minio": "ok"}
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        dependencies["database"] = "error"
    try:
        redis_client.ping()
    except Exception:
        dependencies["redis"] = "error"
    if not storage.ready():
        dependencies["minio"] = "error"
    status = "ok" if all(value == "ok" for value in dependencies.values()) else "error"
    payload = HealthResponse(status=status, dependencies=dependencies)
    if status != "ok":
        return JSONResponse(status_code=503, content=payload.model_dump())
    return payload


@router.get("/metrics")
def metrics(session: Session = Depends(get_db)):
    task_rows = session.execute(
        select(Task.status, Task.task_type, func.count(Task.id)).group_by(
            Task.status, Task.task_type
        )
    ).all()
    TASK_STATE.clear()
    for status, task_type, count in task_rows:
        TASK_STATE.labels(status=status.value, task_type=task_type.value).set(count)

    average_progress = session.scalar(select(func.avg(Task.progress)))
    TASK_PROGRESS.set(float(average_progress or 0))
    pending_outbox = session.scalar(
        select(func.count(OutboxEvent.id)).where(OutboxEvent.status == OutboxStatus.PENDING)
    )
    OUTBOX_PENDING.set(float(pending_outbox or 0))
    open_dead_letters = session.scalar(
        select(func.count(DeadLetter.id)).where(DeadLetter.status == "open")
    )
    DEAD_LETTERS.set(float(open_dead_letters or 0))
    pending_reviews = session.scalar(
        select(func.count(ReviewCase.id)).where(ReviewCase.status == ReviewStatus.PENDING)
    )
    REVIEW_PENDING.set(float(pending_reviews or 0))

    for queue in ("tasks.high", "tasks.normal", "tasks.low", "tasks.dlq"):
        try:
            QUEUE_DEPTH.labels(queue=queue).set(float(redis_client.llen(queue)))
        except Exception:
            QUEUE_DEPTH.labels(queue=queue).set(0)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
