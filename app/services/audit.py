from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditChainHead, AuditEvent


def _json_default(value: Any) -> str:
    if isinstance(value, (UUID, datetime)):
        return str(value)
    raise TypeError(f"无法序列化类型: {type(value)!r}")


def canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default
    )


def compute_event_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    material = f"{prev_hash}|{canonical_payload(payload)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def append_audit_event(
    session: Session,
    *,
    actor_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: str,
    trace_id: str,
    before_value: dict[str, Any] | None = None,
    after_value: dict[str, Any] | None = None,
) -> AuditEvent:
    head = session.scalar(
        select(AuditChainHead).where(AuditChainHead.id == "global").with_for_update()
    )
    if head is None:
        head = AuditChainHead(id="global", last_hash="")
        session.add(head)
        session.flush()

    created_at = datetime.now(UTC)
    payload = {
        "actor_id": str(actor_id) if actor_id else None,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "before": before_value,
        "after": after_value,
        "trace_id": trace_id,
        "created_at": created_at.isoformat(),
    }
    event_hash = compute_event_hash(head.last_hash, payload)
    event = AuditEvent(
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_value=before_value,
        after_value=after_value,
        trace_id=trace_id,
        created_at=created_at,
        prev_hash=head.last_hash,
        event_hash=event_hash,
    )
    session.add(event)
    head.last_hash = event_hash
    session.flush()
    return event


def verify_audit_chain(session: Session) -> tuple[bool, str | None]:
    events = session.scalars(select(AuditEvent).order_by(AuditEvent.id)).all()
    head = session.get(AuditChainHead, "global")
    previous = ""
    for event in events:
        payload = {
            "actor_id": str(event.actor_id) if event.actor_id else None,
            "action": event.action,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "before": event.before_value,
            "after": event.after_value,
            "trace_id": event.trace_id,
            "created_at": event.created_at.isoformat(),
        }
        expected = compute_event_hash(previous, payload)
        if event.prev_hash != previous or event.event_hash != expected:
            return False, str(event.id)
        previous = event.event_hash

    # 只校验事件链本身无法发现“尾部事件被截断”：必须与链头记录的
    # last_hash 对齐。没有事件且没有链头 = 全新库；有事件但无链头 = 异常。
    if head is None:
        return (not events), (str(events[-1].id) if events else None)
    if previous != head.last_hash:
        return False, (str(events[-1].id) if events else "head")
    return True, None
