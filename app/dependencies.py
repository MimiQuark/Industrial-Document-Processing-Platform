from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.domain.enums import Role
from app.errors import AppError
from app.models import User
from app.security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", "unknown")


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_db),
) -> User:
    token = credentials.credentials if credentials else request.cookies.get("access_token")
    if not token:
        raise AppError("AUTH_REQUIRED", "请先登录", 401)
    try:
        user_id: UUID = decode_access_token(token)
    except Exception as exc:
        raise AppError("INVALID_TOKEN", "登录凭证无效或已过期", 401) from exc
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise AppError("USER_INACTIVE", "用户不存在或已停用", 401)
    return user


def require_roles(*roles: Role):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise AppError("FORBIDDEN", "当前用户无权执行该操作", 403)
        return user

    return dependency
