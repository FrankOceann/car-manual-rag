from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.models import User

router = APIRouter()
bearer = HTTPBearer(auto_error=False)
hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return hasher.hash(password)


def user_view(user):
    return {"id": user.id, "username": user.username, "role": user.role, "active": user.active}


def signing_secret():
    secret = Settings().jwt_secret
    if len(secret) < 32:
        raise HTTPException(503, "请配置至少 32 字符的 JWT_SECRET。")
    return secret


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                 session: Session = Depends(get_session)):
    if not credentials:
        raise HTTPException(401, "请先登录。", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(credentials.credentials, signing_secret(), algorithms=["HS256"],
                             issuer="car-manual-rag", options={"require": ["exp", "sub", "iss"]})
        user = session.get(User, payload["sub"])
    except (jwt.InvalidTokenError, KeyError):
        user = None
    if user is None or not user.active:
        raise HTTPException(401, "登录已失效，请重新登录。", headers={"WWW-Authenticate": "Bearer"})
    return user


def admin_user(user: User = Depends(current_user)):
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限。")
    return user


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class NewUser(Login):
    password: str = Field(min_length=10, max_length=256)
    role: Literal["admin", "reader"] = "reader"


class UserUpdate(BaseModel):
    active: bool


@router.post("/auth/login")
def login(body: Login, session: Session = Depends(get_session)):
    secret = signing_secret()
    user = session.scalar(select(User).where(User.username == body.username))
    try:
        valid = user is not None and hasher.verify(user.password_hash, body.password)
    except (VerificationError, InvalidHashError):
        valid = False
    if not valid or not user.active:
        raise HTTPException(401, "用户名或密码错误。")
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": user.id, "iss": "car-manual-rag", "iat": now,
                        "exp": now + timedelta(minutes=Settings().jwt_ttl_minutes)}, secret, algorithm="HS256")
    return {"access_token": token, "token_type": "bearer", "user": user_view(user)}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return user_view(user)


@router.get("/admin/users", dependencies=[Depends(admin_user)])
def users(session: Session = Depends(get_session)):
    return [user_view(u) for u in session.scalars(select(User).order_by(User.username))]


@router.post("/admin/users", status_code=201, dependencies=[Depends(admin_user)])
def create_user(body: NewUser, session: Session = Depends(get_session)):
    if not body.username.strip():
        raise HTTPException(422, "用户名不能为空。")
    user = User(username=body.username.strip(), password_hash=hash_password(body.password), role=body.role)
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "用户名已存在。")
    return user_view(user)


@router.patch("/admin/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, actor: User = Depends(admin_user),
                session: Session = Depends(get_session)):
    if actor.id == user_id and not body.active:
        raise HTTPException(409, "不能停用当前登录账户。")
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "账户不存在。")
    user.active = body.active
    session.commit()
    return user_view(user)
