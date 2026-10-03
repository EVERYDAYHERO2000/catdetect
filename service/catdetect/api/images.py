"""Кадры для обучения и их разметка."""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np
from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlmodel import col, func, select

from ..annotate import read_image
from ..models import Annotation, Identity, Image
from ..vision.detector import crop
from .deps import Auth, Cfg, Db, Rt, not_found

router = APIRouter(prefix="/api/images", tags=["images"])


class AnnotationIn(BaseModel):
    box: list[float] = Field(min_length=4, max_length=4)
    species: Literal["cat", "dog"]
    identity_id: int | None = None


class AnnotationsIn(BaseModel):
    annotations: list[AnnotationIn]


def image_out(img: Image, anns: list[Annotation] | None = None) -> dict:
    d = img.model_dump()
    if anns is not None:
        d["annotations"] = [{"id": a.id, "box": [a.x1, a.y1, a.x2, a.y2], "species": a.species,
                             "identity_id": a.identity_id} for a in anns]
    return d


@router.get("")
def list_images(_: Auth, db: Db, status_: str | None = Query(None, alias="status"), camera_id: int | None = None,
                limit: int = 60, offset: int = 0):
    q = select(Image)
    cq = select(func.count()).select_from(Image)
    if status_:
        q, cq = q.where(Image.status == status_), cq.where(Image.status == status_)
    if camera_id is not None:
        q, cq = q.where(Image.camera_id == camera_id), cq.where(Image.camera_id == camera_id)
    rows = db.exec(q.order_by(col(Image.id).desc()).offset(offset).limit(min(limit, 500))).all()
    return {"total": db.exec(cq).one(), "items": [image_out(i) for i in rows]}


@router.get("/stats")
def stats(_: Auth, db: Db):
    by_status = dict(db.exec(select(Image.status, func.count()).group_by(Image.status)).all())
    by_species = dict(db.exec(select(Annotation.species, func.count()).group_by(Annotation.species)).all())
    by_identity = dict(db.exec(select(Annotation.identity_id, func.count())
                               .where(col(Annotation.identity_id).is_not(None)).group_by(Annotation.identity_id)).all())
    return {"by_status": by_status, "by_species": by_species, "by_identity": by_identity}


@router.get("/{image_id}")
def get_image(image_id: int, _: Auth, db: Db):
    img = db.get(Image, image_id)
    if img is None:
        raise not_found("Кадр")
    anns = db.exec(select(Annotation).where(Annotation.image_id == image_id)).all()
    # соседи для навигации в очереди разметки (по тому же статусу)
    newer = db.exec(select(Image.id).where(Image.id > image_id, Image.status == img.status)
                    .order_by(Image.id).limit(1)).first()
    older = db.exec(select(Image.id).where(Image.id < image_id, Image.status == img.status)
                    .order_by(col(Image.id).desc()).limit(1)).first()
    return {**image_out(img, anns), "prev_id": newer, "next_id": older}


@router.get("/{image_id}/file")
def image_file(image_id: int, _: Auth, db: Db, cfg: Cfg):
    img = db.get(Image, image_id)
    if img is None:
        raise not_found("Кадр")
    path = cfg.data_dir / img.path
    if not path.exists():
        raise not_found("Файл")
    return Response(path.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@router.post("/upload")
async def upload(_: Auth, rt: Rt, files: list[UploadFile] = File(...), camera_id: int | None = None):
    ids = []
    for f in files:
        data = np.frombuffer(await f.read(), dtype=np.uint8)
        frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if frame is None:
            continue
        ids.append(await run_in_threadpool(rt.recorder.save_frame, camera_id, frame, [], "upload"))
    if not ids:
        raise HTTPException(422, "Нет изображений в поддерживаемом формате")
    return {"ids": ids}


@router.put("/{image_id}/annotations")
def save_annotations(image_id: int, body: AnnotationsIn, _: Auth, db: Db):
    """Заменяет разметку кадра. Пустой список — «на кадре нет животных» (негативный пример)."""
    img = db.get(Image, image_id)
    if img is None:
        raise not_found("Кадр")
    for a in db.exec(select(Annotation).where(Annotation.image_id == image_id)).all():
        db.delete(a)
    for a in body.annotations:
        x1, y1, x2, y2 = (min(max(v, 0.0), 1.0) for v in a.box)
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        if x2 - x1 < 0.003 or y2 - y1 < 0.003:
            continue
        if a.identity_id is not None and db.get(Identity, a.identity_id) is None:
            raise HTTPException(422, "Объект не найден")
        db.add(Annotation(image_id=image_id, x1=x1, y1=y1, x2=x2, y2=y2, species=a.species,
                          identity_id=a.identity_id))
    img.status = "labeled"
    db.add(img)
    db.commit()
    return get_image(image_id, _, db)


@router.post("/{image_id}/status")
def set_status(image_id: int, _: Auth, db: Db, value: Literal["unlabeled", "skipped"]):
    img = db.get(Image, image_id)
    if img is None:
        raise not_found("Кадр")
    img.status = value
    db.add(img)
    db.commit()
    return {"ok": True}


@router.delete("/{image_id}")
def delete_image(image_id: int, _: Auth, db: Db, cfg: Cfg):
    img = db.get(Image, image_id)
    if img is None:
        raise not_found("Кадр")
    for a in db.exec(select(Annotation).where(Annotation.image_id == image_id)).all():
        db.delete(a)
    (cfg.data_dir / img.path).unlink(missing_ok=True)
    db.delete(img)
    db.commit()
    return {"ok": True}


@router.post("/{image_id}/predict")
async def predict(image_id: int, _: Auth, db: Db, rt: Rt, cfg: Cfg, min_conf: float = 0.2):
    """Предразметка текущей моделью (детектор + классификатор личности)."""
    img = db.get(Image, image_id)
    if img is None:
        raise not_found("Кадр")
    if rt.detector is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Модель ещё не загружена")
    frame = read_image(cfg.data_dir / img.path)
    if frame is None:
        raise not_found("Файл")

    def run():
        out = []
        for d in rt.detector.detect(frame, ("cat", "dog"), min_conf):
            item = {"box": list(d.box), "species": d.species, "conf": round(d.conf, 3), "identity_id": None}
            if rt.identifier is not None:
                ident, p = rt.identifier.identify(crop(frame, d.box))
                item["identity_id"], item["identity_conf"] = ident, round(p, 3)
            out.append(item)
        return out

    return {"predictions": await run_in_threadpool(run)}
