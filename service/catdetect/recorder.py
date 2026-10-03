"""Запись событий и кадров для разметки (файлы в каталоге данных + строки в БД)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np
from sqlalchemy.engine import Engine
from sqlmodel import Session

from .annotate import write_jpeg
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
                   snapshot: bytes | None) -> dict[str, Any]:
        now = utcnow()
        rel = None
        if snapshot is not None:
            day, stamp = self._stamp(now)
            rel = f"events/{camera_id}/{day}/{stamp}_{kind}.jpg"
            write_jpeg(self.settings.data_dir / rel, snapshot)
        ev = Event(camera_id=camera_id, ts=now, kind=kind, species=species, identity_id=identity_id,
                   confidence=confidence, identity_confidence=identity_confidence, track_id=track_id,
                   snapshot_path=rel)
        with Session(self.engine) as s:
            s.add(ev)
            s.commit()
            s.refresh(ev)
            return event_to_dict(ev)

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
            return img.id
