"""Раздел «Объекты»: папки с вырезками (объекты), «Неразобранные» и «Не объект»."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import col, func, select

from ..labels import ASSIGNED, PENDING, REJECTED, move
from ..models import Annotation, Identity, Image, MlModel
from .deps import Auth, Db

router = APIRouter(prefix="/api/objects", tags=["objects"])

MIN_SAMPLES = 5  # минимум снимков в папке для обучения классификатора


@router.get("/summary")
def summary(_: Auth, db: Db):
    """Папки и счётчики + подсказка, пора ли переобучить."""
    by_state = dict(db.exec(select(Annotation.state, func.count()).group_by(Annotation.state)).all())
    by_ident = dict(db.exec(select(Annotation.identity_id, func.count())
                            .where(Annotation.state == ASSIGNED, col(Annotation.identity_id).is_not(None))
                            .group_by(Annotation.identity_id)).all())
    last_cls = db.exec(select(MlModel).where(MlModel.kind == "classifier").order_by(col(MlModel.id).desc())).first()
    q = select(func.count()).select_from(Annotation).where(Annotation.state == ASSIGNED)
    if last_cls is not None:
        q = q.where(Annotation.assigned_at > last_cls.created_at)
    new_since = db.exec(q).one()
    # кадры без единой карточки (например, загруженные фото, где модель никого не нашла)
    empty_q = select(Image.id).where(Image.status == "unlabeled", ~col(Image.id).in_(select(Annotation.image_id)))
    empty_frames = db.exec(select(func.count()).select_from(empty_q.subquery())).one()
    first_empty = db.exec(empty_q.order_by(col(Image.id).desc()).limit(1)).first()
    idents = db.exec(select(Identity).order_by(Identity.species, Identity.name)).all()
    ready = sum(1 for i in idents if by_ident.get(i.id, 0) >= MIN_SAMPLES)
    return {
        "pending": by_state.get(PENDING, 0),
        "rejected": by_state.get(REJECTED, 0),
        "empty_frames": empty_frames,
        "first_empty_frame_id": first_empty,
        "folders": [{**i.model_dump(), "count": by_ident.get(i.id, 0)} for i in idents],
        "training": {
            "min_samples": MIN_SAMPLES,
            "ready_folders": ready,
            "can_train": ready >= 2,
            "new_since_last": new_since,
            "has_model": last_cls is not None,
        },
    }


@router.get("/crops")
def crops(_: Auth, db: Db, folder: str = PENDING, camera_id: int | None = None, limit: int = 120, offset: int = 0):
    """folder: pending | rejected | <identity_id>."""
    q = select(Annotation, Image).join(Image, Image.id == Annotation.image_id)
    if folder in (PENDING, REJECTED):
        q = q.where(Annotation.state == folder)
    else:
        try:
            q = q.where(Annotation.state == ASSIGNED, Annotation.identity_id == int(folder))
        except ValueError as e:
            raise HTTPException(422, "Неизвестная папка") from e
    if camera_id is not None:
        q = q.where(Image.camera_id == camera_id)
    total = db.exec(select(func.count()).select_from(q.subquery())).one()
    rows = db.exec(q.order_by(col(Image.captured_at).desc(), col(Annotation.id)).offset(offset)
                   .limit(min(limit, 500))).all()
    return {
        "total": total,
        "items": [{
            "id": a.id, "image_id": img.id, "camera_id": img.camera_id, "captured_at": img.captured_at,
            "species": a.species, "conf": a.conf, "box": [a.x1, a.y1, a.x2, a.y2],
            "identity_id": a.identity_id, "suggested_identity_id": a.suggested_identity_id,
        } for a, img in rows],
    }


class MoveIn(BaseModel):
    ids: list[int]
    target: Literal["pending", "rejected"] | int


@router.post("/move")
def move_crops(body: MoveIn, _: Auth, db: Db):
    """Переложить карточки в папку объекта, в «Неразобранные» или в «Не объект»."""
    try:
        n, skipped = move(db, body.ids, body.target)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    db.commit()
    return {"moved": n, "skipped": skipped}


class IdsIn(BaseModel):
    ids: list[int]


@router.post("/accept_suggestions")
def accept_suggestions(body: IdsIn, _: Auth, db: Db):
    """Разложить выбранные карточки по подсказкам классификатора («Барсик?» → Барсик).
    Подсказка другого вида (собака → «Илья») не принимается."""
    moved = 0
    groups: dict[int, list[int]] = {}
    species = dict(db.exec(select(Identity.id, Identity.species)).all())
    for a in db.exec(select(Annotation).where(col(Annotation.id).in_(body.ids))).all():
        if a.suggested_identity_id and species.get(a.suggested_identity_id) == a.species:
            groups.setdefault(a.suggested_identity_id, []).append(a.id)
    skipped = 0
    for ident_id, ids in groups.items():
        try:
            n, s = move(db, ids, ident_id)
        except ValueError:
            continue
        moved += n
        skipped += s
    db.commit()
    return {"moved": moved, "skipped": skipped}
