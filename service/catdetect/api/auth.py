from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlmodel import func, select

from ..auth import SESSION_COOKIE, SESSION_MAX_AGE, authenticate, hash_password, hash_token, new_token, verify_password
from ..models import ApiToken, User
from .deps import Db, UserAuth, not_found

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)


class LoginBody(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=256)


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class TokenOut(BaseModel):
    id: int
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None


def _set_session(request: Request, response: Response, user_id: int) -> None:
    response.set_cookie(
        SESSION_COOKIE, request.app.state.signer.dumps(user_id), max_age=SESSION_MAX_AGE, httponly=True,
        samesite="lax", secure=request.app.state.settings.cookie_secure,
    )


@router.get("/status")
def auth_status(request: Request, db: Db):
    has_users = db.exec(select(func.count()).select_from(User)).one() > 0
    p = authenticate(request, db, request.app.state.signer)
    return {"setup_required": not has_users, "authenticated": p is not None,
            "username": p.name if p else None, "kind": p.kind if p else None}


@router.post("/setup")
def setup(body: Credentials, request: Request, response: Response, db: Db):
    """Создание первого (единственного) администратора."""
    if db.exec(select(func.count()).select_from(User)).one() > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Администратор уже создан")
    user = User(username=body.username, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    _set_session(request, response, user.id)
    return {"ok": True, "username": user.username}


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response, db: Db):
    user = db.exec(select(User).where(User.username == body.username)).first()
    if user is None or not verify_password(user.password_hash, body.password):
        await asyncio.sleep(1.0)  # замедляем перебор
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    _set_session(request, response, user.id)
    return {"ok": True, "username": user.username}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.post("/password")
def change_password(body: PasswordChange, p: UserAuth, db: Db):
    user = db.get(User, p.id)
    if user is None or not verify_password(user.password_hash, body.old_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Текущий пароль неверен")
    user.password_hash = hash_password(body.new_password)
    db.add(user)
    db.commit()
    return {"ok": True}


@router.get("/tokens", response_model=list[TokenOut])
def list_tokens(_: UserAuth, db: Db):
    return db.exec(select(ApiToken).order_by(ApiToken.id)).all()


@router.post("/tokens")
def create_token(body: TokenCreate, _: UserAuth, db: Db):
    """Токен показывается один раз; в БД хранится только хеш."""
    token = new_token()
    row = ApiToken(name=body.name, token_hash=hash_token(token), prefix=token[:7])
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "name": row.name, "token": token}


@router.delete("/tokens/{token_id}")
def delete_token(token_id: int, _: UserAuth, db: Db):
    row = db.get(ApiToken, token_id)
    if row is None:
        raise not_found("Токен")
    db.delete(row)
    db.commit()
    return {"ok": True}
