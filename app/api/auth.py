from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.dependencies import get_current_user
from app.errors import AppError
from app.models import User
from app.schemas import LoginRequest, TokenResponse, UserRead
from app.security import create_access_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, response: Response, session: Session = Depends(get_db)):
    user = session.scalar(select(User).where(User.email == payload.email.lower().strip()))
    password_valid = user and verify_password(payload.password, user.hashed_password)
    if user is None or not user.is_active or not password_valid:
        raise AppError("INVALID_CREDENTIALS", "邮箱或密码错误", 401)
    token = create_access_token(user.id)
    settings = get_settings()
    response.set_cookie(
        "access_token",
        token,
        max_age=settings.access_token_minutes * 60,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "production",
    )
    return TokenResponse(access_token=token)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"message": "已退出登录"}


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)):
    return user
