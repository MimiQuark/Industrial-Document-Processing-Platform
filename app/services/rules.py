from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import RuleDefinition, SystemState, User
from app.schemas import RuleCreate, RulePatch


def list_latest_rules(session: Session) -> list[RuleDefinition]:
    rules = session.scalars(
        select(RuleDefinition).order_by(RuleDefinition.code, RuleDefinition.version.desc())
    ).all()
    latest: dict[str, RuleDefinition] = {}
    for rule in rules:
        latest.setdefault(rule.code, rule)
    return sorted(latest.values(), key=lambda item: item.code)


def _next_version(session: Session) -> int:
    state = session.get(SystemState, "global", with_for_update=True)
    if state is None:
        state = SystemState(id="global", rule_version=1)
        session.add(state)
        session.flush()
        return 1
    state.rule_version += 1
    session.flush()
    return state.rule_version


def create_rule(session: Session, payload: RuleCreate, actor: User) -> RuleDefinition:
    existing = session.scalar(
        select(RuleDefinition).where(RuleDefinition.code == payload.code).limit(1)
    )
    if existing is not None:
        raise AppError("RULE_EXISTS", "规则编码已存在，请使用更新接口", 409)
    rule = RuleDefinition(
        **payload.model_dump(),
        version=_next_version(session),
        created_by=actor.id,
    )
    session.add(rule)
    session.flush()
    return rule


def update_rule(session: Session, rule_id: UUID, payload: RulePatch, actor: User) -> RuleDefinition:
    current = session.get(RuleDefinition, rule_id)
    if current is None:
        raise AppError("RULE_NOT_FOUND", "规则不存在", 404)
    latest = session.scalar(
        select(RuleDefinition)
        .where(RuleDefinition.code == current.code)
        .order_by(RuleDefinition.version.desc())
        .limit(1)
    )
    if latest is None or latest.id != current.id:
        raise AppError("RULE_VERSION_STALE", "只能更新规则的最新版本", 409)
    values = current_values(current)
    values.update(payload.model_dump(exclude_none=True))
    rule = RuleDefinition(
        **values,
        version=_next_version(session),
        created_by=actor.id,
    )
    session.add(rule)
    session.flush()
    return rule


def current_values(rule: RuleDefinition) -> dict:
    return {
        "code": rule.code,
        "name": rule.name,
        "description": rule.description,
        "file_type": rule.file_type,
        "severity": rule.severity,
        "enabled": rule.enabled,
        "config": rule.config,
    }
