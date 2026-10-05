from __future__ import annotations

import json
from contextlib import suppress
from typing import Any
from uuid import UUID

import redis

from app.config import get_settings

redis_client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


def task_channel(task_id: UUID | str) -> str:
    return f"task:{task_id}:events"


def publish_task_event(task_id: UUID | str, payload: dict[str, Any]) -> None:
    with suppress(Exception):
        # SSE is best effort; PostgreSQL remains the source of truth.
        redis_client.publish(task_channel(task_id), json.dumps(payload, default=str))


def push_dead_letter(task_id: UUID | str, payload: dict[str, Any]) -> None:
    with suppress(Exception):
        redis_client.lpush(
            "tasks.dlq", json.dumps({"task_id": str(task_id), **payload}, default=str)
        )
