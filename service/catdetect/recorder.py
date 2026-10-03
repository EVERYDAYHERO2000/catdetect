"""Запись событий и кадров для разметки (файлы в каталоге данных + строки в БД)."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from typing import Any

import numpy as np
from sqlalchemy.engine import Engine
from sqlmodel import Session

from .annotate import read_image, write_jpeg
from .labels import add_pending
from .models import Event, Image, utcnow
from .settings import Settings

log = logging.getLogger(__name__)


def event_to_dict(ev: Event) -> dict[str, Any]:
    return {
        "id": ev.id,
        "camera_id": ev.camera_id,
        "ts": ev.ts.isoformat() if ev.ts else None,
        "kind": ev.kind,
        "species": ev.species,
        "identity_id": ev.identity_id,
        "confidence": round(ev.confidence, 3),
        "identity_confidence": None if ev.identity_confidence is None else round(ev.identity_confidence, 3),
        "track_id": ev.track_id,
        "has_snapshot": bool(ev.snapshot_path),
        "details": ev.details,
        "has_raw": bool(ev.raw_path),
        "image_id": ev.image_id,
    }


class Recorder:
    def __init__(self, settings: Settings, engine: Engine):
        self.settings = settings
        self.engine = engine

    @staticmethod
    def _stamp(now: datetime) -> tuple[str, str]:
        local = now.astimezone()
        return local.strftime("%Y-%m-%d"), local.strftime("%H%M%S_%f")

    def save_event(self, camera_id: int, kind: str, species: str, identity_id: int | None,
                   confidence: float, identity_confidence: float | None, track_id: int,
                   snapshot: bytes | None, details: dict[str, Any] | None = None,
                   raw: np.ndarray | None = None, predictions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        now = utcnow()
        day, stamp = self._stamp(now)
        rel = raw_rel = None
        if snapshot is not None:
            rel = f"events/{camera_id}/{day}/{stamp}_{kind}.jpg"
            write_jpeg(self.settings.data_dir / rel, snapshot)
        if raw is not None:
            raw_rel = f"events/{camera_id}/{day}/{stamp}_{kind}_raw.jpg"
            write_jpeg(self.settings.data_dir / raw_rel, raw, quality=95)
        ev = Event(camera_id=camera_id, ts=now, kind=kind, species=species, identity_id=identity_id,
                   confidence=confidence, identity_confidence=identity_confidence, track_id=track_id,
                   snapshot_path=rel, details=details, raw_path=raw_rel, predictions=predictions)
        with Session(self.engine) as s:
            s.add(ev)
            s.commit()
            s.refresh(ev)
            return event_to_dict(ev)

    def event_to_image(self, event_id: int) -> int | None:
        """Кадр события → очередь разметки (один раз; повторно возвращается тот же кадр)."""
        with Session(self.engine) as s:
            ev = s.get(Event, event_id)
            if ev is None or not ev.raw_path:
                return None
            if ev.image_id is not None and s.get(Image, ev.image_id) is not None:
                return ev.image_id
            src = self.settings.data_dir / ev.raw_path
            if not src.exists():
                return None
            frame = read_image(src)
            day, stamp = self._stamp(ev.ts)
            rel = f"images/{ev.camera_id}/{day}/{stamp}_ev{ev.id}.jpg"
            dst = self.settings.data_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            h, w = frame.shape[:2]
            img = Image(camera_id=ev.camera_id, path=rel, width=w, height=h, captured_at=ev.ts, source="event",
                        predictions=ev.predictions or [])
            s.add(img)
            s.commit()
            s.refresh(img)
            add_pending(s, img, ev.predictions or [])
            ev.image_id = img.id
            s.add(ev)
            s.commit()
            return img.id

    def save_frame(self, camera_id: int | None, frame: np.ndarray, predictions: list[dict[str, Any]],
                   source: str = "auto") -> int:
        now = utcnow()
        day, stamp = self._stamp(now)
        rel = f"images/{camera_id or 'upload'}/{day}/{stamp}.jpg"
        write_jpeg(self.settings.data_dir / rel, frame, quality=95)
        h, w = frame.shape[:2]
        img = Image(camera_id=camera_id, path=rel, width=w, height=h, captured_at=now, source=source,
                    predictions=predictions)
        with Session(self.engine) as s:
            s.add(img)
            s.commit()
            s.refresh(img)
            add_pending(s, img, predictions)  # найденные объекты → «Неразобранные»
            s.commit()
            return img.id
