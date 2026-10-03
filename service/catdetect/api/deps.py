from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session

from ..auth import Principal, SessionSigner, authenticate, unauthorized
from ..runtime import Runtime
from ..settings import Settings


def get_db(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as s:
        yield s


def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_signer(request: Request) -> SessionSigner:
    return request.app.state.signer


Db = Annotated[Session, Depends(get_db)]
Rt = Annotated[Runtime, Depends(get_runtime)]
Cfg = Annotated[Settings, Depends(get_settings)]


def require_auth(request: Request, db: Db) -> Principal:
    p = authenticate(request, db, request.app.state.signer)
    if p is None:
        raise unauthorized()
    return p


def require_user(p: Annotated[Principal, Depends(require_auth)]) -> Principal:
    """Только вход по паролю (управление токенами и паролем недоступно по API-токену)."""
    if p.kind != "user":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Доступно только после входа по паролю")
    return p


Auth = Annotated[Principal, Depends(require_auth)]
UserAuth = Annotated[Principal, Depends(require_user)]


def reload_config_async(rt: Runtime) -> None:
    """Перезапуск потоков камер не должен блокировать HTTP-ответ."""
    threading.Thread(target=rt.reload_config, name="reload-config", daemon=True).start()


def not_found(what: str = "Объект") -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} не найден")
