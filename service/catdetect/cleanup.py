"""Срок хранения: всё, что не разобрано, удаляется через N часов (по умолчанию 48).

Удаляются (старше срока):
- события — вместе со снимками (история событий показывает только последние N часов);
- неразобранные карточки в «Объектах» (раздел «Неразобранные»);
- кадры, на которых после этого не осталось ни одной карточки и которые не размечены.

Остаются только разобранные снимки: карточки в папках объектов и в «Не объект», а также кадры,
отмеченные «Нет объектов» (осознанные примеры для обучения).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .labels import PENDING, refresh_image_status
from .models import Annotation, AppSetting, Event, Image, utcnow

log = logging.getLogger(__name__)

DEFAULT_HOURS = 48
RUN_EVERY = 3600  # сек


def _unlink(data_dir: Path, rel: str | None) -> None:
    if not rel:
        return
    p = data_dir / rel
    try:
        p.unlink(missing_ok=True)
    except OSError as e:
        log.warning("Не удалось удалить %s: %s", p, e)
        return
    # пустые папки дней и камер тоже убираем
    for parent in (p.parent, p.parent.parent):
        try:
            if parent != data_dir and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass


def run_cleanup(engine: Engine, data_dir: Path, hours: float) -> dict[str, Any]:
    cutoff = utcnow() - timedelta(hours=hours)
    with Session(engine) as db:
        # 1. история событий
        old_events = db.exec(select(Event).where(Event.ts < cutoff)).all()
        for ev in old_events:
            _unlink(data_dir, ev.snapshot_path)
            _unlink(data_dir, ev.raw_path)
            db.delete(ev)

        # 2. неразобранные карточки
        pending = db.exec(select(Annotation).join(Image, Image.id == Annotation.image_id).where(
            Annotation.state == PENDING, Image.captured_at < cutoff)).all()
        touched = {a.image_id for a in pending}
        for a in pending:
            db.delete(a)
        db.flush()
        refresh_image_status(db, touched)  # кадр с оставшимися разобранными карточками становится «размеченным»
        db.flush()

        # 3. кадры без карточек, которые не размечены (в т.ч. оставшиеся после шага 2)
        # кадры, где были только неразобранные карточки, удаляем независимо от статуса:
        # refresh_image_status пометил бы пустой кадр «размеченным», как «Нет объектов»
        frames = db.exec(select(Image).where(
            (col(Image.status).in_(["unlabeled", "skipped"]) | col(Image.id).in_(touched or [-1])),
            Image.captured_at < cutoff,
            ~col(Image.id).in_(select(Annotation.image_id)),
        )).all()
        for img in frames:
            for ev in db.exec(select(Event).where(Event.image_id == img.id)).all():
                ev.image_id = None
                db.add(ev)
            _unlink(data_dir, img.path)
            db.delete(img)
        db.commit()
    result = {"events": len(old_events), "crops": len(pending), "images": len(frames), "hours": hours}
    if old_events or pending or frames:
        log.info("Очистка (старше %g ч): событий %d, неразобранных карточек %d, кадров %d",
                 hours, len(old_events), len(pending), len(frames))
    return result


def retention_hours(engine: Engine) -> float:
    with Session(engine) as db:
        row = db.get(AppSetting, "retention_hours")
    try:
        return float(row.value) if row is not None else DEFAULT_HOURS
    except (TypeError, ValueError):
        return DEFAULT_HOURS


class Cleaner(threading.Thread):
    """Фоновая очистка раз в час. 0 часов в настройках — хранить всё."""

    def __init__(self, engine: Engine, data_dir: Path):
        super().__init__(name="cleanup", daemon=True)
        self.engine = engine
        self.data_dir = data_dir
        self.stop_event = threading.Event()
        self.last_run: float | None = None
        self.last_result: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def run_now(self) -> dict[str, Any] | None:
        hours = retention_hours(self.engine)
        if hours <= 0:
            return None
        with self._lock:
            try:
                self.last_result = run_cleanup(self.engine, self.data_dir, hours)
            except Exception:  # noqa: BLE001
                log.exception("Ошибка очистки старых снимков")
                return None
            self.last_run = time.time()
            return self.last_result

    def run(self) -> None:
        self.stop_event.wait(60)  # дать сервису стартовать
        while not self.stop_event.is_set():
            self.run_now()
            self.stop_event.wait(RUN_EVERY)
