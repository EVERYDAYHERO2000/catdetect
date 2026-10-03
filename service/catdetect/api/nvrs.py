from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlmodel import select

from ..models import Camera, Nvr
from ..annotate import to_jpeg
from ..nvr.dahua import get_device_info, get_snapshot
from ..nvr.reader import grab_frame
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


_snap_cache: dict[tuple[int, int, int], tuple[float, bytes]] = {}
SNAP_TTL = 20.0


def _channel_snapshot(n: Nvr, channel: int) -> bytes | None:
    spec = nvr_spec(n)
    data = get_snapshot(spec, channel)
    if data is None:  # запасной путь — кадр из дополнительного RTSP-потока
        frame = grab_frame(spec.rtsp_url(channel, "sub"), timeout=8)
        data = to_jpeg(frame) if frame is not None else None
    return data


@router.get("/{nvr_id}/channels/{channel}/snapshot")
async def channel_snapshot(nvr_id: int, channel: int, _: Auth, db: Db):
    """Превью канала для выбора камеры (кешируется на 20 с)."""
    n = db.get(Nvr, nvr_id)
    if n is None:
        raise not_found("Регистратор")
    key = (nvr_id, channel, hash((n.host, n.http_port, n.username, n.password)))
    cached = _snap_cache.get(key)
    if cached and time.monotonic() - cached[0] < SNAP_TTL:
        data = cached[1]
    else:
        data = await run_in_threadpool(_channel_snapshot, n, channel)
        if data is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Нет изображения с канала")
        _snap_cache[key] = (time.monotonic(), data)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


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
