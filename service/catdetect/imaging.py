"""Отдача сохранённых снимков с учётом пропорций, заданных в настройках камеры.

Старые снимки (до выбора пропорций) хранятся как есть; при выдаче они приводятся к пропорциям камеры,
поэтому превью, вырезки, кадры для обучения и снимки для HA везде выглядят одинаково.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Response
from fastapi.responses import FileResponse
from sqlmodel import Session

import cv2

from .annotate import read_image, to_jpeg
from .models import Camera
from .nvr.reader import apply_aspect

NO_CACHE = {"Cache-Control": "no-cache"}


def camera_aspect(db: Session, camera_id: int | None) -> str | None:
    cam = db.get(Camera, camera_id) if camera_id else None
    return cam.aspect if cam else None


def load_frame(path: Path, aspect: str | None):
    frame = read_image(path)
    return apply_aspect(frame, aspect) if frame is not None else None


def image_response(path: Path, aspect: str | None, max_side: int | None = None) -> Response:
    """max_side — уменьшенная копия для превью (быстро и легко)."""
    if not aspect and not max_side:
        return FileResponse(path, media_type="image/jpeg", headers=NO_CACHE)
    frame = read_image(path)
    if frame is None:
        return FileResponse(path, media_type="image/jpeg", headers=NO_CACHE)
    out = apply_aspect(frame, aspect)
    if max_side:
        h, w = out.shape[:2]
        k = max_side / max(h, w)
        if k < 1:
            out = cv2.resize(out, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
    if out is frame:  # ничего не менялось
        return FileResponse(path, media_type="image/jpeg", headers=NO_CACHE)
    return Response(to_jpeg(out, 82 if max_side else 90), media_type="image/jpeg", headers=NO_CACHE)
