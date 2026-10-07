"""Раскладка найденных объектов по папкам (раздел «Объекты»)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .models import Annotation, Identity, Image, utcnow

PENDING, ASSIGNED, REJECTED = "pending", "assigned", "rejected"


def add_pending(db: Session, image: Image, predictions: Iterable[dict[str, Any]]) -> int:
    """Детекции модели → карточки в «Неразобранных». Возвращает число карточек.
    Подсказку «кто это» сохраняем, только если вид папки совпадает с видом карточки."""
    species = dict(db.exec(select(Identity.id, Identity.species)).all())
    used: set[int] = set()
    n = 0
    for p in sorted(predictions, key=lambda p: -(p.get("identity_conf") or p.get("conf") or 0)):
        x1, y1, x2, y2 = p["box"]
        if x2 - x1 < 0.003 or y2 - y1 < 0.003:
            continue
        db.add(Annotation(image_id=image.id, x1=x1, y1=y1, x2=x2, y2=y2, species=p["species"], state=PENDING,
                          conf=p.get("conf"),
                          suggested_identity_id=_suggest(p, species, used)))
        n += 1
    return n


def _suggest(p: dict[str, Any], species: dict[int, str], used: set[int]) -> int | None:
    ident = p.get("identity_id")
    if ident is None or species.get(ident) != p["species"] or ident in used:
        return None
    used.add(ident)
    return ident


def refresh_image_status(db: Session, image_ids: Iterable[int]) -> None:
    """Кадр «размечен», когда на нём не осталось неразобранных карточек."""
    for image_id in set(image_ids):
        img = db.get(Image, image_id)
        if img is None:
            continue
        states = db.exec(select(Annotation.state).where(Annotation.image_id == image_id)).all()
        img.status = "unlabeled" if PENDING in states else "labeled"
        db.add(img)


def move(db: Session, annotation_ids: list[int], target: str | int) -> tuple[int, int]:
    """target: "pending" | "rejected" | identity_id. Возвращает (перемещено, пропущено).

    В папку объекта с одного кадра попадает не больше одной карточки: два «Ильи» на кадре быть не может.
    """
    ident = None
    if target not in (PENDING, REJECTED):
        ident = db.get(Identity, int(target))
        if ident is None:
            raise ValueError("Папка не найдена")
    touched = []
    skipped = 0
    taken: set[int] = set()  # кадры, где это имя уже есть
    if ident is not None:
        taken = set(db.exec(select(Annotation.image_id).where(Annotation.identity_id == ident.id,
                                                             Annotation.state == ASSIGNED)).all())
    rows = db.exec(select(Annotation).where(col(Annotation.id).in_(annotation_ids))).all()
    # при нескольких карточках с одного кадра первой идёт самая уверенная
    rows = sorted(rows, key=lambda a: -(a.conf or 0))
    for a in rows:
        if ident is not None:
            if a.identity_id == ident.id and a.state == ASSIGNED:
                continue
            if a.image_id in taken:
                skipped += 1
                continue
            taken.add(a.image_id)
            a.state, a.identity_id, a.species, a.assigned_at = ASSIGNED, ident.id, ident.species, utcnow()
        else:
            a.state, a.identity_id, a.assigned_at = target, None, None
        db.add(a)
        touched.append(a.image_id)
    db.flush()
    refresh_image_status(db, touched)
    return len(touched), skipped


def normalize_legacy(engine: Engine) -> None:
    """Старые записи (до раздела «Объекты»): разметка с объектом → в папку, детекции неразмеченных кадров → в
    «Неразобранные». Идемпотентно."""
    with Session(engine) as db:
        # подсказки другого вида (собака → «Илья») — следствие старой ошибки, убираем
        species = dict(db.exec(select(Identity.id, Identity.species)).all())
        for a in db.exec(select(Annotation).where(col(Annotation.suggested_identity_id).is_not(None))).all():
            if species.get(a.suggested_identity_id) != a.species:
                a.suggested_identity_id = None
                db.add(a)
        seen: set[tuple[int, int]] = set()
        for a in db.exec(select(Annotation).where(col(Annotation.suggested_identity_id).is_not(None))
                         .order_by(Annotation.image_id, col(Annotation.conf).desc())).all():
            key = (a.image_id, a.suggested_identity_id)
            if key in seen:
                a.suggested_identity_id = None  # то же имя у второй карточки кадра
                db.add(a)
            seen.add(key)
        legacy = db.exec(select(Annotation).where(col(Annotation.state).is_(None))).all()
        for a in legacy:
            a.state = ASSIGNED if a.identity_id else PENDING
            db.add(a)
        with_anns = set(db.exec(select(Annotation.image_id)).all())
        fresh = [i for i in db.exec(select(Image).where(Image.status == "unlabeled")).all()
                 if i.id not in with_anns and i.predictions]
        for img in fresh:
            add_pending(db, img, img.predictions)
        db.flush()
        refresh_image_status(db, [a.image_id for a in legacy] + [i.id for i in fresh])
        db.commit()
