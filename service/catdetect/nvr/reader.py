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


def grab_frame(url: str, timeout: float = 10.0) -> np.ndarray | None:
    """Одиночный кадр (для превью, когда поток камеры не запущен)."""
    cap = cv2.VideoCapture(os.path.expanduser(url), cv2.CAP_FFMPEG)
    try:
        if not cap.isOpened():
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ok, frame = cap.read()
            if ok and frame is not None:
                return frame
        return None
    finally:
        cap.release()


class StreamReader(threading.Thread):
    """Держит соединение открытым и переподключается. Видеофайл крутится по кругу
    с родной скоростью — удобно для отладки без регистратора."""

    def __init__(self, url: str, name: str, stop_event: threading.Event | None = None):
        super().__init__(name=f"reader-{name}", daemon=True)
        self.url = url
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
            cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG)
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
            fails = 0
            while not self.stop_event.is_set():
                t0 = time.monotonic()
                ok, frame = cap.read()
                if not ok or frame is None:
                    if is_file:
                        break  # файл закончился — открываем заново
                    fails += 1
                    if fails > 50:
                        log.warning("Поток %s перестал отдавать кадры", redact_url(self.url))
                        break
                    self.stop_event.wait(0.1)
                    continue
                fails = 0
                with self._lock:
                    self._frame = frame
                    self._frame_id += 1
                    self._frame_ts = time.monotonic()
                if frame_delay:
                    self.stop_event.wait(max(0.0, frame_delay - (time.monotonic() - t0)))
            cap.release()
            self.connected = False
