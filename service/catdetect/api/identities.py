from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlmodel import col, func, select

from ..annotate import to_jpeg
from ..imaging import camera_aspect, load_frame
from ..labels import PENDING, refresh_image_status
from ..models import Annotation, Event, Identity, Image
from ..vision.detector import crop
from .deps import Auth, Cfg, Db, Rt, not_found, reload_config_async

router = APIRouter(prefix="/api/identities", tags=["identities"])


class IdentityIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    species: Literal["cat", "dog", "person"] = "cat"
    is_own: bool = True
    notes: str = ""


@router.get("")
def list_identities(_: Auth, db: Db):
    counts = dict(db.exec(select(Annotation.identity_id, func.count()).where(col(Annotation.identity_id).is_not(None))
                          .group_by(Annotation.identity_id)).all())
    return [{**i.model_dump(), "samples": counts.get(i.id, 0)}
            for i in db.exec(select(Identity).order_by(Identity.id)).all()]


@router.post("")
def create_identity(body: IdentityIn, _: Auth, db: Db, rt: Rt):
    i = Identity(**body.model_dump())
    db.add(i)
    db.commit()
    db.refresh(i)
    reload_config_async(rt)
    return i


@router.put("/{identity_id}")
def update_identity(identity_id: int, body: IdentityIn, _: Auth, db: Db, rt: Rt):
    i = db.get(Identity, identity_id)
    if i is None:
        raise not_found("Объект")
    for k, v in body.model_dump().items():
        setattr(i, k, v)
    db.add(i)
    db.commit()
    db.refresh(i)
    reload_config_async(rt)
    return i


@router.delete("/{identity_id}")
def delete_identity(identity_id: int, _: Auth, db: Db, rt: Rt):
    i = db.get(Identity, identity_id)
    if i is None:
        raise not_found("Объект")
    touched = []
    for a in db.exec(select(Annotation).where(Annotation.identity_id == identity_id)).all():
        a.identity_id, a.state, a.assigned_at = None, PENDING, None  # снимки папки возвращаются в «Неразобранные»
        db.add(a)
        touched.append(a.image_id)
    for a in db.exec(select(Annotation).where(Annotation.suggested_identity_id == identity_id)).all():
        a.suggested_identity_id = None
        db.add(a)
    db.flush()
    refresh_image_status(db, touched)
    for ev in db.exec(select(Event).where(Event.identity_id == identity_id)).all():
        ev.identity_id = None
        db.add(ev)
    db.delete(i)
    db.commit()
    reload_config_async(rt)
    return {"ok": True}


@router.get("/{identity_id}/samples")
def identity_samples(identity_id: int, _: Auth, db: Db, limit: int = 24):
    rows = db.exec(select(Annotation).where(Annotation.identity_id == identity_id)
                   .order_by(col(Annotation.id).desc()).limit(limit)).all()
    return [{"annotation_id": a.id, "image_id": a.image_id} for a in rows]


@router.get("/crops/{annotation_id}")
def annotation_crop(annotation_id: int, _: Auth, db: Db, cfg: Cfg):
    a = db.get(Annotation, annotation_id)
    img = db.get(Image, a.image_id) if a else None
    if img is None:
        raise not_found("Пример")
    frame = load_frame(cfg.data_dir / img.path, camera_aspect(db, img.camera_id))
    if frame is None:
        raise not_found("Файл")
    return Response(to_jpeg(crop(frame, (a.x1, a.y1, a.x2, a.y2), pad=0.1)), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})
