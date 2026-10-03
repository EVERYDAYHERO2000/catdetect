from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlmodel import select

from ..models import Camera, Nvr
from ..nvr.dahua import get_device_info
from ..runtime import nvr_spec
from .deps import Auth, Db, Rt, not_found, reload_config_async

router = APIRouter(prefix="/api/nvrs", tags=["nvrs"])


class NvrIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    host: str = Field(min_length=1, max_length=255)
    http_port: int = Field(80, ge=1, le=65535)
    rtsp_port: int = Field(554, ge=1, le=65535)
    https: bool = False
    username: str = "admin"
    password: str | None = None  # None при обновлении — не менять
    event_codes: list[str] = ["VideoMotion"]
    enabled: bool = True


def nvr_out(n: Nvr, rt=None) -> dict:
    d = n.model_dump(exclude={"password"})
    d["has_password"] = bool(n.password)
    if rt is not None:
        d["events_connected"] = rt.listener_status().get(n.id)
    return d


@router.get("")
def list_nvrs(_: Auth, db: Db, rt: Rt):
    return [nvr_out(n, rt) for n in db.exec(select(Nvr).order_by(Nvr.id)).all()]


@router.post("")
def create_nvr(body: NvrIn, _: Auth, db: Db, rt: Rt):
    n = Nvr(**body.model_dump(exclude={"password"}), password=body.password or "")
    db.add(n)
    db.commit()
    db.refresh(n)
    reload_config_async(rt)
    return nvr_out(n)


@router.put("/{nvr_id}")
def update_nvr(nvr_id: int, body: NvrIn, _: Auth, db: Db, rt: Rt):
    n = db.get(Nvr, nvr_id)
    if n is None:
        raise not_found("Регистратор")
    for k, v in body.model_dump(exclude={"password"}).items():
        setattr(n, k, v)
    if body.password is not None:
        n.password = body.password
    db.add(n)
    db.commit()
    db.refresh(n)
    reload_config_async(rt)
    return nvr_out(n)


@router.delete("/{nvr_id}")
def delete_nvr(nvr_id: int, _: Auth, db: Db, rt: Rt):
    n = db.get(Nvr, nvr_id)
    if n is None:
        raise not_found("Регистратор")
    if db.exec(select(Camera).where(Camera.nvr_id == nvr_id)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Сначала удалите камеры этого регистратора")
    db.delete(n)
    db.commit()
    reload_config_async(rt)
    return {"ok": True}


async def _probe(n: Nvr) -> dict:
    try:
        info = await run_in_threadpool(get_device_info, nvr_spec(n))
        return {"ok": True, **info}
    except httpx.HTTPStatusError as e:
        msg = "Неверный логин или пароль" if e.response.status_code == 401 else f"HTTP {e.response.status_code}"
        return {"ok": False, "error": msg}
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"Нет связи: {e}"}


@router.post("/test")
async def test_new_nvr(body: NvrIn, _: Auth):
    """Проверка параметров до сохранения."""
    return await _probe(Nvr(**body.model_dump(exclude={"password"}), password=body.password or ""))


@router.post("/{nvr_id}/test")
async def test_nvr(nvr_id: int, _: Auth, db: Db):
    n = db.get(Nvr, nvr_id)
    if n is None:
        raise not_found("Регистратор")
    return await _probe(n)
