"""Аутентификация: cookie-сессия для веб-интерфейса и Bearer API-токены (для HA)."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, WebSocket, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlmodel import Session, select

from .models import ApiToken, User, utcnow

SESSION_COOKIE = "catdetect_session"
SESSION_MAX_AGE = 30 * 24 * 3600
TOKEN_PREFIX = "cd_"

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Principal:
    kind: str  # user | token
    id: int
    name: str


class SessionSigner:
    def __init__(self, secret_key: str):
        self._s = URLSafeTimedSerializer(secret_key, salt="catdetect-session")

    def dumps(self, user_id: int) -> str:
        return self._s.dumps({"uid": user_id})

    def loads(self, value: str) -> int | None:
        try:
            return int(self._s.loads(value, max_age=SESSION_MAX_AGE)["uid"])
        except (BadSignature, KeyError, TypeError, ValueError):
            return None


def authenticate(conn: Request | WebSocket, db: Session, signer: SessionSigner) -> Principal | None:
    header = conn.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        row = db.exec(select(ApiToken).where(ApiToken.token_hash == hash_token(token))).first()
        if row is None:
            return None
        row.last_used_at = utcnow()
        db.add(row)
        db.commit()
        return Principal("token", row.id, row.name)
    cookie = conn.cookies.get(SESSION_COOKIE)
    if cookie:
        uid = signer.loads(cookie)
        if uid is not None:
            user = db.get(User, uid)
            if user is not None:
                return Principal("user", user.id, user.username)
    return None


def unauthorized() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход", headers={"WWW-Authenticate": "Bearer"})
