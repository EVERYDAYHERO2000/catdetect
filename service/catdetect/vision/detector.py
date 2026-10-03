"""Этап 1 — детекция кошек/собак (YOLO). Этап 2 — распознавание конкретного животного по кропу (YOLO-cls).

ultralytics импортируется лениво: API и тесты работают без torch.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from pathlib import Path

import numpy as np

from .geometry import Box
from .tracker import Detection

log = logging.getLogger(__name__)


def crop(frame: np.ndarray, box: Box, pad: float = 0.15) -> np.ndarray:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    x1, x2 = max(0.0, x1 - bw * pad), min(1.0, x2 + bw * pad)
    y1, y2 = max(0.0, y1 - bh * pad), min(1.0, y2 + bh * pad)
    return frame[int(y1 * h):max(int(y2 * h), int(y1 * h) + 1), int(x1 * w):max(int(x2 * w), int(x1 * w) + 1)]


class Detector:
    def __init__(self, weights: str | Path, device: str | None = None, imgsz: int = 640):
        from ultralytics import YOLO

        self.weights = str(weights)
        self.device = device
        self.imgsz = imgsz
        self.model = YOLO(self.weights)
        self._lock = threading.Lock()  # одна модель на все камеры
        self.names: dict[int, str] = dict(self.model.names)
        self._ids = {name: i for i, name in self.names.items()}
        log.info("Детектор загружен: %s", self.weights)

    def class_ids(self, species: Iterable[str]) -> list[int]:
        return [self._ids[s] for s in species if s in self._ids]

    def detect(self, frame: np.ndarray, species: Iterable[str], min_conf: float = 0.25) -> list[Detection]:
        classes = self.class_ids(species)
        if not classes:
            return []
        with self._lock:
            res = self.model.predict(
                frame, conf=min_conf, classes=classes, imgsz=self.imgsz, device=self.device, verbose=False
            )[0]
        out: list[Detection] = []
        if res.boxes is None:
            return out
        for xyxyn, cls, conf in zip(res.boxes.xyxyn.tolist(), res.boxes.cls.tolist(), res.boxes.conf.tolist()):
            out.append(Detection(self.names[int(cls)], float(conf), tuple(float(v) for v in xyxyn)))
        return out

    def warmup(self) -> None:
        self.detect(np.zeros((480, 640, 3), dtype=np.uint8), self.names.values())


class Identifier:
    """Классификатор личности. Имена классов модели сопоставлены с Identity.id через class_map."""

    def __init__(self, weights: str | Path, class_map: dict[str, int], device: str | None = None):
        from ultralytics import YOLO

        self.model = YOLO(str(weights))
        self.device = device
        self.class_map = class_map
        self._lock = threading.Lock()
        log.info("Классификатор загружен: %s (%d классов)", weights, len(class_map))

    def identify(self, crop_img: np.ndarray) -> tuple[int | None, float]:
        """identity_id (None — класс без привязки, напр. «фон») и вероятность."""
        if crop_img.size == 0:
            return None, 0.0
        with self._lock:
            res = self.model.predict(crop_img, device=self.device, verbose=False)[0]
        top = int(res.probs.top1)
        name = res.names[top]
        return self.class_map.get(name), float(res.probs.top1conf)
