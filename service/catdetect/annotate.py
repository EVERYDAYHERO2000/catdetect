"""Отрисовка зон, линии направления и боксов на кадре; сохранение JPEG (безопасно для не-ASCII путей)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np

COLORS = {"cat": (0, 200, 255), "dog": (255, 160, 0), "person": (120, 220, 60)}


def _px(p: Sequence[float], w: int, h: int) -> tuple[int, int]:
    return int(p[0] * w), int(p[1] * h)


def draw_overlay(
    frame: np.ndarray,
    zone: list[list[float]] | None = None,
    direction: dict[str, Any] | None = None,
    boxes: Iterable[dict[str, Any]] = (),
) -> np.ndarray:
    """boxes: [{"box": (x1,y1,x2,y2), "species": str, "label": str, "confirmed": bool}]"""
    img = frame.copy()
    h, w = img.shape[:2]
    k = max(1.0, max(h, w) / 640)  # толщина и шрифт пропорционально размеру кадра
    if zone:
        pts = np.array([_px(p, w, h) for p in zone], dtype=np.int32)
        cv2.polylines(img, [pts], True, (0, 255, 0), round(2 * k))
    if direction and direction.get("line"):
        a, b = direction["line"]
        cv2.line(img, _px(a, w, h), _px(b, w, h), (255, 0, 255), round(2 * k))
        if direction.get("door_point"):
            cv2.circle(img, _px(direction["door_point"], w, h), round(8 * k), (255, 0, 255), -1)
    for item in boxes:
        x1, y1, x2, y2 = item["box"]
        color = COLORS.get(item.get("species", ""), (200, 200, 200))
        thick = round((2 if item.get("confirmed", True) else 1) * k)
        cv2.rectangle(img, _px((x1, y1), w, h), _px((x2, y2), w, h), color, thick)
        label = item.get("label")
        if label:
            # кириллицу cv2.putText не умеет — подписи только латиницей/цифрами
            org = _px((x1, y1), w, h)
            y = max(round(16 * k), org[1] - round(6 * k))
            for c, t in (((0, 0, 0), round(3 * k)), (color, max(1, round(k)))):  # с обводкой для читаемости
                cv2.putText(img, label, (org[0], y), cv2.FONT_HERSHEY_SIMPLEX, 0.55 * k, c, t, cv2.LINE_AA)
    return img


def to_jpeg(img: np.ndarray, quality: int = 85) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encode failed")
    return buf.tobytes()


def write_jpeg(path: Path, img: np.ndarray | bytes, quality: int = 90) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = img if isinstance(img, bytes) else to_jpeg(img, quality)
    path.write_bytes(data)


def read_image(path: Path) -> np.ndarray | None:
    data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)
