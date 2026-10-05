from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_trace_id, require_roles
from app.domain.enums import Role
from app.errors import AppError
from app.models import AuditEvent, DeadLetter, User
from app.schemas import (
    AuditEventRead,
    AuditListResponse,
    AuditVerifyResponse,
    DeadLetterRead,
    RuleCreate,
    RulePatch,
    RuleRead,
)
from app.services.audit import append_audit_event, verify_audit_chain
from app.services.rules import create_rule, list_latest_rules, update_rule
from app.services.tasks import retry_task

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/rules", response_model=list[RuleRead])
def rules(
    _: User = Depends(require_roles(Role.ADMIN)),
    session: Session = Depends(get_db),
):
    return [RuleRead.model_validate(item) for item in list_latest_rules(session)]


@router.post("/rules", response_model=RuleRead)
def create_new_rule(
    payload: RuleCreate,
    user: User = Depends(require_roles(Role.ADMIN)),
    session: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id),
):
    rule = create_rule(session, payload, user)
    append_audit_event(
        session,
        actor_id=user.id,
        action="rule.created",
        resource_type="rule",
        resource_id=str(rule.id),
        trace_id=trace_id,
        after_value={"code": rule.code, "version": rule.version, "config": rule.config},
    )
    session.commit()
    session.refresh(rule)
    return RuleRead.model_validate(rule)


@router.patch("/rules/{rule_id}", response_model=RuleRead)
def patch_rule(
    rule_id: UUID,
    payload: RulePatch,
    user: User = Depends(require_roles(Role.ADMIN)),
    session: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id),
):
    rule = update_rule(session, rule_id, payload, user)
    append_audit_event(
        session,
        actor_id=user.id,
        action="rule.version_created",
        resource_type="rule",
        resource_id=str(rule.id),
        trace_id=trace_id,
        after_value={"code": rule.code, "version": rule.version, "config": rule.config},
    )
    session.commit()
    session.refresh(rule)
    return RuleRead.model_validate(rule)


@router.get("/dead-letters", response_model=list[DeadLetterRead])
def dead_letters(
    _: User = Depends(require_roles(Role.ADMIN)),
    session: Session = Depends(get_db),
):
    items = session.scalars(
        select(DeadLetter).order_by(DeadLetter.created_at.desc()).limit(100)
    ).all()
    return [DeadLetterRead.model_validate(item) for item in items]


@router.post("/dead-letters/{dead_letter_id}/requeue", response_model=DeadLetterRead)
def requeue_dead_letter(
    dead_letter_id: UUID,
    user: User = Depends(require_roles(Role.ADMIN)),
    session: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id),
):
    dead_letter = session.get(DeadLetter, dead_letter_id)
    if dead_letter is None:
        raise AppError("DEAD_LETTER_NOT_FOUND", "死信记录不存在", 404)
    if dead_letter.status == "requeued":
        raise AppError("ALREADY_REQUEUED", "该死信已经重新入队", 409)
    retry_task(session, dead_letter.task_id, user, trace_id)
    dead_letter.status = "requeued"
    append_audit_event(
        session,
        actor_id=user.id,
        action="dead_letter.requeued",
        resource_type="dead_letter",
        resource_id=str(dead_letter.id),
        trace_id=trace_id,
        after_value={"task_id": str(dead_letter.task_id)},
    )
    session.commit()
    session.refresh(dead_letter)
    return DeadLetterRead.model_validate(dead_letter)


@router.get("/audit", response_model=AuditListResponse)
def audit_events(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_roles(Role.ADMIN)),
    session: Session = Depends(get_db),
):
    total = session.scalar(select(func.count(AuditEvent.id))) or 0
    items = session.scalars(
        select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit).offset(offset)
    ).all()
    return AuditListResponse(
        items=[AuditEventRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/audit/verify", response_model=AuditVerifyResponse)
def verify_audit(
    _: User = Depends(require_roles(Role.ADMIN)),
    session: Session = Depends(get_db),
):
    valid, broken = verify_audit_chain(session)
    return AuditVerifyResponse(valid=valid, broken_event_id=broken)
