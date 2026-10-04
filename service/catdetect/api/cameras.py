from __future__ import annotations

from typing import Any, Literal

import cv2
import numpy as np

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator
from sqlmodel import select

from ..annotate import draw_overlay, to_jpeg
from ..imaging import image_response
from ..models import SPECIES, Camera, Event, Image, Nvr
from ..nvr.reader import apply_aspect, grab_frame
from ..runtime import camera_url
from ..vision.geometry import DirectionRule
from .deps import Auth, Db, Rt, not_found, reload_config_async

router = APIRouter(prefix="/api/cameras", tags=["cameras"])

Point = list[float]


class DirectionIn(BaseModel):
    line: list[Point] = Field(min_length=2, max_length=2)
    door_point: Point
    margin: float = Field(0.02, ge=0, le=0.2)


class CameraIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9_]{1,32}$")
    name: str = Field(min_length=1, max_length=64)
    nvr_id: int | None = None
    channel: int = Field(1, ge=1, le=256)
    stream: Literal["main", "sub"] = "sub"
    source_url: str | None = None
    trigger: Literal["motion", "always"] = "motion"
    fps: float = Field(5.0, gt=0, le=30)
    linger: float = Field(10.0, ge=0, le=600)
    clear_after: float = Field(10.0, ge=0, le=600)
    species: list[str] = ["cat", "dog"]
    zone: list[Point] | None = None
    direction: DirectionIn | None = None
    min_conf: float = Field(0.25, gt=0, lt=1)
    confirm_hits: int = Field(3, ge=1, le=50)
    confirm_conf: float = Field(0.5, gt=0, lt=1)
    identity_conf: float = Field(0.6, gt=0, lt=1)
    save_frames: bool = True
    keep_stream: bool = True
    aspect: str | None = Field(None, pattern=r"^\d{1,4}(\.\d{1,3})?:\d{1,4}(\.\d{1,3})?$")
    enabled: bool = True

    @field_validator("species")
    @classmethod
    def _species(cls, v: list[str]) -> list[str]:
        bad = [s for s in v if s not in SPECIES]
        if bad or not v:
            raise ValueError(f"Допустимые виды: {', '.join(SPECIES)}")
        return v

    @field_validator("zone")
    @classmethod
    def _zone(cls, v):
        if v is not None and len(v) < 3:
            return None  # незамкнутый полигон — без зоны
        return v

    @field_validator("aspect", mode="before")
    @classmethod
    def _aspect(cls, v):
        return (v.strip().replace("/", ":") or None) if isinstance(v, str) else v

    @field_validator("source_url")
    @classmethod
    def _src(cls, v):
        return v.strip() or None if v else None


def _validate(body: CameraIn, db) -> None:
    if body.nvr_id is None and not body.source_url:
        raise HTTPException(422, "Укажите регистратор или URL источника")
    if body.nvr_id is not None and db.get(Nvr, body.nvr_id) is None:
        raise HTTPException(422, "Регистратор не найден")
    if body.direction is not None:
        try:
            DirectionRule(body.direction.line, body.direction.door_point, body.direction.margin)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e


def _apply(cam: Camera, body: CameraIn) -> None:
    data = body.model_dump()
    data["direction"] = body.direction.model_dump() if body.direction else None
    for k, v in data.items():
        setattr(cam, k, v)


def camera_out(c: Camera) -> dict[str, Any]:
    return c.model_dump()


@router.get("")
def list_cameras(_: Auth, db: Db):
    return [camera_out(c) for c in db.exec(select(Camera).order_by(Camera.id)).all()]


@router.get("/{camera_id}")
def get_camera(camera_id: int, _: Auth, db: Db):
    c = db.get(Camera, camera_id)
    if c is None:
        raise not_found("Камера")
    return camera_out(c)


@router.post("")
def create_camera(body: CameraIn, _: Auth, db: Db, rt: Rt):
    _validate(body, db)
    if db.exec(select(Camera).where(Camera.slug == body.slug)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Идентификатор камеры уже занят")
    c = Camera(slug=body.slug, name=body.name)
    _apply(c, body)
    db.add(c)
    db.commit()
    db.refresh(c)
    reload_config_async(rt)
    return camera_out(c)


@router.put("/{camera_id}")
def update_camera(camera_id: int, body: CameraIn, _: Auth, db: Db, rt: Rt):
    c = db.get(Camera, camera_id)
    if c is None:
        raise not_found("Камера")
    _validate(body, db)
    other = db.exec(select(Camera).where(Camera.slug == body.slug)).first()
    if other and other.id != camera_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Идентификатор камеры уже занят")
    _apply(c, body)
    db.add(c)
    db.commit()
    db.refresh(c)
    reload_config_async(rt)
    return camera_out(c)


@router.delete("/{camera_id}")
def delete_camera(camera_id: int, _: Auth, db: Db, rt: Rt):
    c = db.get(Camera, camera_id)
    if c is None:
        raise not_found("Камера")
    for ev in db.exec(select(Event).where(Event.camera_id == camera_id)).all():
        db.delete(ev)
    for img in db.exec(select(Image).where(Image.camera_id == camera_id)).all():
        img.camera_id = None  # кадры для обучения сохраняем
        db.add(img)
    db.delete(c)
    db.commit()
    reload_config_async(rt)
    return {"ok": True}


async def _frame(camera_id: int, db, rt):
    c = db.get(Camera, camera_id)
    if c is None:
        raise not_found("Камера")
    frame = rt.latest_frame(camera_id)
    if frame is None:
        url = camera_url(c, db.get(Nvr, c.nvr_id) if c.nvr_id else None)
        if url:
            frame = await run_in_threadpool(grab_frame, url, 10.0, c.aspect)
    if frame is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Нет кадра с камеры")
    return c, frame


@router.get("/{camera_id}/stream_info")
async def stream_info(camera_id: int, _: Auth, db: Db, rt: Rt):
    """Размер кадра в потоке (до приведения пропорций) — подсказка для выбора пропорций."""
    c = db.get(Camera, camera_id)
    if c is None:
        raise not_found("Камера")
    w = rt.worker(camera_id)
    native = w.reader.native_size if w is not None and w.reader is not None else None
    if native is None:
        url = camera_url(c, db.get(Nvr, c.nvr_id) if c.nvr_id else None)
        frame = await run_in_threadpool(grab_frame, url) if url else None
        native = (frame.shape[1], frame.shape[0]) if frame is not None else None
    return {"native": native, "aspect": c.aspect}


@router.get("/{camera_id}/snapshot")
async def live_snapshot(camera_id: int, _: Auth, db: Db, rt: Rt, overlay: bool = False):
    """Живой кадр (для редактора зоны и превью)."""
    c, frame = await _frame(camera_id, db, rt)
    if overlay:
        frame = draw_overlay(frame, c.zone, c.direction)
    return Response(to_jpeg(frame), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/{camera_id}/last_snapshot")
def last_snapshot(camera_id: int, _: Auth, rt: Rt, db: Db):
    """Снимок последнего события с разметкой (для сущности image в HA)."""
    snap = rt.state.last_snapshot(camera_id)
    if snap is not None:
        c = db.get(Camera, camera_id)
        data = snap[1]
        if c and c.aspect:
            frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            fixed = apply_aspect(frame, c.aspect) if frame is not None else None
            if fixed is not None and fixed is not frame:
                data = to_jpeg(fixed, 90)
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    ev = db.exec(select(Event).where(Event.camera_id == camera_id, Event.snapshot_path != None)  # noqa: E711
                 .order_by(Event.ts.desc())).first()
    if ev is None:
        raise not_found("Снимок")
    c = db.get(Camera, camera_id)
    return image_response(rt.settings.data_dir / ev.snapshot_path, c.aspect if c else None)


@router.get("/{camera_id}/debug")
async def debug_view(camera_id: int, _: Auth, rt: Rt):
    """Живой просмотр: что видит детектор прямо сейчас (включая слабые и отброшенные зоной рамки)."""
    w = rt.worker(camera_id)
    if w is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Камера выключена или ещё запускается")
    img = await run_in_threadpool(w.debug_image)
    if img is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Нет кадра или модель ещё не загружена")
    return Response(to_jpeg(img, 80), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/{camera_id}/detect")
async def test_detect(camera_id: int, _: Auth, db: Db, rt: Rt):
    """Прогнать детектор на текущем кадре с текущими настройками камеры."""
    if rt.detector is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Модель ещё не загружена")
    c, frame = await _frame(camera_id, db, rt)
    dets = await run_in_threadpool(rt.detector.detect, frame, c.species, c.min_conf)
    return {"detections": [{"box": d.box, "species": d.species, "conf": round(d.conf, 3)} for d in dets]}


class TestEventIn(BaseModel):
    kind: Literal["seen", "arrived", "left"] = "arrived"
    species: Literal["cat", "dog", "person"] = "cat"
    identity_id: int | None = None


@router.post("/{camera_id}/test_event")
async def test_event(camera_id: int, body: TestEventIn, _: Auth, db: Db, rt: Rt):
    """Сымитировать событие (для настройки автоматизаций HA): пишется в историю как обычное."""
    c = db.get(Camera, camera_id)
    if c is None:
        raise not_found("Камера")
    frame = rt.latest_frame(camera_id)
    snapshot = to_jpeg(draw_overlay(frame, c.zone, c.direction)) if frame is not None else None
    record = await run_in_threadpool(rt.recorder.save_event, camera_id, body.kind, body.species, body.identity_id,
                                     1.0, 1.0 if body.identity_id else None, 0, snapshot, None, frame, [])
    rt.state.on_event(record, snapshot)
    return record
