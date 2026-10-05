from __future__ import annotations

from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.domain.enums import Role, RuleSeverity, TaskType
from app.models import AuditChainHead, RuleDefinition, SystemState, User
from app.security import hash_password

DEFAULT_RULES = [
    {
        "code": "PDF_PAGES_MAX",
        "name": "PDF 页数限制",
        "description": "页数超过阈值时转人工复核",
        "file_type": TaskType.PDF,
        "severity": RuleSeverity.WARNING,
        "config": {"metric": "page_count", "operator": "gt", "value": 200},
    },
    {
        "code": "PDF_ENCRYPTED",
        "name": "PDF 加密检查",
        "description": "加密 PDF 无法可靠解析，直接失败",
        "file_type": TaskType.PDF,
        "severity": RuleSeverity.CRITICAL,
        "config": {"metric": "is_encrypted", "operator": "truthy", "value": True},
    },
    {
        "code": "PDF_NO_TEXT",
        "name": "PDF 文本缺失",
        "description": "未提取到文本时转人工复核",
        "file_type": TaskType.PDF,
        "severity": RuleSeverity.WARNING,
        "config": {"metric": "text_char_count", "operator": "lt", "value": 1},
    },
    {
        "code": "SVG_SCRIPT",
        "name": "SVG 脚本检查",
        "description": "SVG 中禁止包含脚本元素",
        "file_type": TaskType.SVG,
        "severity": RuleSeverity.CRITICAL,
        "config": {"metric": "script_count", "operator": "gt", "value": 0},
    },
    {
        "code": "SVG_EXTERNAL_REFERENCE",
        "name": "SVG 外部引用检查",
        "description": "外部引用需要人工确认",
        "file_type": TaskType.SVG,
        "severity": RuleSeverity.WARNING,
        "config": {"metric": "external_ref_count", "operator": "gt", "value": 0},
    },
    {
        "code": "SVG_DIMENSIONS_MISSING",
        "name": "SVG 尺寸缺失",
        "description": "没有明确尺寸信息时转人工复核",
        "file_type": TaskType.SVG,
        "severity": RuleSeverity.WARNING,
        "config": {"metric": "has_dimensions", "operator": "falsy", "value": True},
    },
    {
        "code": "GERBER_FORMAT_MISSING",
        "name": "Gerber 格式定义检查",
        "description": "缺少 FS 格式定义属于严重错误",
        "file_type": TaskType.GERBER,
        "severity": RuleSeverity.CRITICAL,
        "config": {"metric": "has_format", "operator": "falsy", "value": True},
    },
    {
        "code": "GERBER_UNITS_MISSING",
        "name": "Gerber 单位定义检查",
        "description": "缺少 MO 单位定义时转人工复核",
        "file_type": TaskType.GERBER,
        "severity": RuleSeverity.WARNING,
        "config": {"metric": "has_units", "operator": "falsy", "value": True},
    },
    {
        "code": "GERBER_END_MISSING",
        "name": "Gerber 结束标记检查",
        "description": "缺少 M02 结束标记属于严重错误",
        "file_type": TaskType.GERBER,
        "severity": RuleSeverity.CRITICAL,
        "config": {"metric": "has_end", "operator": "falsy", "value": True},
    },
    {
        "code": "GERBER_UNKNOWN_COMMANDS",
        "name": "Gerber 未知命令检查",
        "description": "出现无法识别的行时转人工复核",
        "file_type": TaskType.GERBER,
        "severity": RuleSeverity.WARNING,
        "config": {"metric": "unknown_line_count", "operator": "gt", "value": 0},
    },
]


def _ensure_user(session, *, email: str, password: str, role: Role, name: str) -> User:
    user = session.scalar(select(User).where(User.email == email))
    if user:
        return user
    user = User(
        email=email,
        display_name=name,
        hashed_password=hash_password(password),
        role=role,
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def seed_defaults() -> None:
    settings = get_settings()
    with session_scope() as session:
        audit_head = session.get(AuditChainHead, "global")
        if audit_head is None:
            session.add(AuditChainHead(id="global", last_hash=""))
            session.flush()

        state = session.get(SystemState, "global")
        if state is None:
            state = SystemState(id="global", rule_version=1)
            session.add(state)
            session.flush()

        _ensure_user(
            session,
            email=settings.admin_email,
            password=settings.admin_password,
            role=Role.ADMIN,
            name="平台管理员",
        )
        _ensure_user(
            session,
            email=settings.reviewer_email,
            password=settings.reviewer_password,
            role=Role.REVIEWER,
            name="审核员",
        )
        _ensure_user(
            session,
            email=settings.submitter_email,
            password=settings.submitter_password,
            role=Role.SUBMITTER,
            name="文件提交人",
        )

        for definition in DEFAULT_RULES:
            existing = session.scalar(
                select(RuleDefinition).where(RuleDefinition.code == definition["code"]).limit(1)
            )
            if existing is None:
                session.add(RuleDefinition(**definition, version=state.rule_version, enabled=True))


if __name__ == "__main__":
    seed_defaults()
