"""Чтение видеопотока в отдельном потоке: всегда доступен последний кадр."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

log = logging.getLogger(__name__)


def redact_url(url: str) -> str:
    return re.sub(r"//[^/@]*@", "//***@", url)


def is_file_source(url: str) -> bool:
    return "://" not in url and Path(url).expanduser().exists()


OPEN_TIMEOUT_MS = 10_000
READ_TIMEOUT_MS = 10_000  # без таймаута чтение RTSP при обрыве может висеть десятки минут
STALL_RECONNECT = 15  # сек без кадров — переоткрыть поток


def open_capture(url: str) -> cv2.VideoCapture:
    return cv2.VideoCapture(os.path.expanduser(url), cv2.CAP_FFMPEG, [
        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS,
        cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS,
    ])


def parse_aspect(aspect: str | None) -> float | None:
    """«16:9» → 1.777…; None/пусто/ошибка — пропорции потока как есть."""
    if not aspect:
        return None
    try:
        w, h = (float(v) for v in aspect.split(":"))
        return w / h if w > 0 and h > 0 else None
    except ValueError:
        return None


def apply_aspect(frame: np.ndarray, aspect: str | None) -> np.ndarray:
    """Привести кадр к заданным пропорциям (камеры с неквадратными пикселями отдают сплющенную картинку).
    Масштабируется только одна сторона и только в меньшую — без лишнего увеличения кадра."""
    ratio = parse_aspect(aspect)
    if ratio is None:
        return frame
    h, w = frame.shape[:2]
    if abs(w / h - ratio) < 0.01:
        return frame
    if ratio > w / h:
        size = (w, max(1, round(w / ratio)))  # нужно шире — уменьшаем высоту
    else:
        size = (max(1, round(h * ratio)), h)  # нужно уже — уменьшаем ширину
    return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)


def grab_frame(url: str, timeout: float = 10.0, aspect: str | None = None) -> np.ndarray | None:
    """Одиночный кадр (для превью, когда поток камеры не запущен)."""
    cap = open_capture(url)
    try:
        if not cap.isOpened():
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ok, frame = cap.read()
            if ok and frame is not None:
                return apply_aspect(frame, aspect)
        return None
    finally:
        cap.release()


class StreamReader(threading.Thread):
    """Держит соединение открытым и переподключается. Видеофайл крутится по кругу
    с родной скоростью — удобно для отладки без регистратора."""

    def __init__(self, url: str, name: str, stop_event: threading.Event | None = None, aspect: str | None = None):
        super().__init__(name=f"reader-{name}", daemon=True)
        self.url = url
        self.aspect = aspect
        self.native_size: tuple[int, int] | None = None  # (ширина, высота) кадра в потоке
        self.stop_event = stop_event or threading.Event()
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._frame_id = 0
        self._frame_ts = 0.0
        self.connected = False

    def latest(self) -> tuple[np.ndarray | None, int, float]:
        with self._lock:
            return self._frame, self._frame_id, self._frame_ts

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        is_file = is_file_source(self.url)
        src = os.path.expanduser(self.url)
        backoff = 1.0
        while not self.stop_event.is_set():
            cap = open_capture(src)
            if not cap.isOpened():
                log.warning("Не удалось открыть поток %s", redact_url(self.url))
                cap.release()
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            backoff = 1.0
            self.connected = True
            log.info("Поток открыт: %s", redact_url(self.url))
            frame_delay = 0.0
            if is_file:
                fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
                frame_delay = 1.0 / fps
            last_ok = time.monotonic()
            while not self.stop_event.is_set():
                t0 = time.monotonic()
                ok, frame = cap.read()
                if not ok or frame is None:
                    if is_file:
                        break  # файл закончился — открываем заново
                    if time.monotonic() - last_ok > STALL_RECONNECT:
                        log.warning("Поток %s не отдаёт кадры %d с — переподключаюсь", redact_url(self.url),
                                    STALL_RECONNECT)
                        break
                    self.stop_event.wait(0.1)
                    continue
                last_ok = time.monotonic()
                with self._lock:
                    self.native_size = (frame.shape[1], frame.shape[0])
                    self._frame = apply_aspect(frame, self.aspect)
                    self._frame_id += 1
                    self._frame_ts = time.monotonic()
                if frame_delay:
                    self.stop_event.wait(max(0.0, frame_delay - (time.monotonic() - t0)))
            cap.release()
            self.connected = False
