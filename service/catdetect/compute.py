"""Выбор вычислителя: процессор (OpenVINO/ONNX/torch) или видеокарта (CUDA у NVIDIA, MPS у Apple)."""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger(__name__)

PREFERENCES = ("auto", "cpu", "gpu")


@dataclass(frozen=True)
class Compute:
    kind: str  # cpu | gpu
    device: str  # аргумент device для ultralytics: cpu | cuda:0 | mps
    fmt: str  # torch | openvino | onnx
    label: str  # для интерфейса

    def as_dict(self) -> dict:
        return {"kind": self.kind, "device": self.device, "format": self.fmt, "label": self.label}


@lru_cache(maxsize=1)
def detect_gpu() -> tuple[str, str] | None:
    """(device, название) доступной видеокарты или None."""
    try:
        import torch
    except ImportError:
        return None
    if torch.cuda.is_available():
        return "cuda:0", torch.cuda.get_device_name(0)
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps", "Apple GPU (Metal)"
    return None


def cpu_format(preferred: str) -> str:
    """Формат для CPU: OpenVINO/ONNX, если библиотека установлена, иначе обычный torch."""
    if preferred == "openvino" and importlib.util.find_spec("openvino") is None:
        log.warning("OpenVINO не установлен — инференс на CPU через torch")
        return "torch"
    if preferred == "onnx" and importlib.util.find_spec("onnxruntime") is None:
        log.warning("onnxruntime не установлен — инференс на CPU через torch")
        return "torch"
    return preferred if preferred in ("openvino", "onnx") else "torch"


def resolve(preference: str, cpu_fmt: str = "openvino") -> Compute:
    gpu = detect_gpu()
    if preference in ("auto", "gpu") and gpu is not None:
        return Compute("gpu", gpu[0], "torch", gpu[1])
    if preference == "gpu":
        log.warning("Видеокарта недоступна — используется процессор")
    fmt = cpu_format(cpu_fmt)
    return Compute("cpu", "cpu", fmt, f"Процессор ({'OpenVINO' if fmt == 'openvino' else fmt.upper() if fmt == 'onnx' else 'PyTorch'})")


def available() -> dict:
    gpu = detect_gpu()
    return {"cpu": True, "gpu": gpu[1] if gpu else None}
