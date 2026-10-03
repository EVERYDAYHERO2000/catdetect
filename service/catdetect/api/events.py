from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlmodel import Session, col, select

from ..auth import authenticate
from ..models import Camera, Event, Identity
from ..recorder import event_to_dict
from .deps import Auth, Cfg, Db, Rt, not_found

log = logging.getLogger(__name__)
router = APIRouter(tags=["events"])


@router.get("/api/events")
def list_events(_: Auth, db: Db, camera_id: int | None = None, identity_id: int | None = None,
                species: str | None = None, kind: str | None = None,
                before_id: int | None = None, limit: int = 50):
    q = select(Event)
    if species is not None:
        q = q.where(Event.species == species)
    if kind == "detections":  # всё, кроме журнала движения
        q = q.where(Event.kind != "motion")
    elif kind is not None:
        q = q.where(Event.kind == kind)
    if camera_id is not None:
        q = q.where(Event.camera_id == camera_id)
    if identity_id is not None:
        q = q.where(Event.identity_id == identity_id)
    if before_id is not None:
        q = q.where(Event.id < before_id)
    rows = db.exec(q.order_by(col(Event.id).desc()).limit(min(limit, 500))).all()
    return [event_to_dict(e) for e in rows]


@router.get("/api/events/{event_id}/image")
def event_image(event_id: int, _: Auth, db: Db, cfg: Cfg):
    ev = db.get(Event, event_id)
    if ev is None or not ev.snapshot_path:
        raise not_found("Снимок")
    path = cfg.data_dir / ev.snapshot_path
    if not path.exists():
        raise not_found("Снимок")
    # no-cache + ETag: id событий могут переиспользоваться после удаления — браузер всегда сверяет версию
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.post("/api/events/{event_id}/label")
async def label_event(event_id: int, _: Auth, rt: Rt):
    """Отправить исходный кадр события в разметку (с рамками модели как подсказкой)."""
    image_id = await run_in_threadpool(rt.recorder.event_to_image, event_id)
    if image_id is None:
        raise not_found("Исходный кадр события")
    return {"image_id": image_id}


@router.get("/api/state")
def full_state(_: Auth, db: Db, rt: Rt):
    """Полное состояние для интеграции HA и дашборда: конфигурация + текущие значения."""
    st = rt.state.snapshot()
    cams = db.exec(select(Camera).order_by(Camera.id)).all()
    idents = db.exec(select(Identity).order_by(Identity.id)).all()
    return {
        "service": {"version": __import__("catdetect").__version__, "detector": rt.detector_status()},
        "cameras": [{
            "id": c.id, "slug": c.slug, "name": c.name, "species": c.species, "enabled": c.enabled,
            "has_direction": bool(c.direction), "state": st["cameras"].get(c.id),
        } for c in cams],
        "identities": [{
            "id": i.id, "name": i.name, "species": i.species, "is_own": i.is_own,
            "state": st["identities"].get(i.id),
        } for i in idents],
    }


@router.websocket("/api/ws")
async def websocket(ws: WebSocket):
    with Session(ws.app.state.engine) as db:
        principal = authenticate(ws, db, ws.app.state.signer)
    if principal is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    bus = ws.app.state.bus
    q = bus.subscribe()

    async def pump():
        while True:
            msg = await q.get()
            await ws.send_text(json.dumps(msg, default=str))

    async def keepalive():
        # принимаем ping от клиента, чтобы заметить закрытие соединения
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text('{"type":"pong"}')

    tasks = [asyncio.create_task(pump()), asyncio.create_task(keepalive())]
    try:
        await ws.send_text(json.dumps({"type": "hello", "user": principal.name}))
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            if t.exception() and not isinstance(t.exception(), WebSocketDisconnect):
                log.debug("WebSocket закрыт: %s", t.exception())
    finally:
        for t in tasks:
            t.cancel()
        bus.unsubscribe(q)
