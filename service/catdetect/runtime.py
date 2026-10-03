"""Оркестрация: модели, потоки камер, подписки на события регистраторов."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .events import EventBus
from .models import Camera, Event, Identity, MlModel, Nvr
from .nvr.dahua import DahuaEvent, DahuaEventListener, NvrSpec
from .pipeline import CameraSpec, CameraWorker
from .recorder import Recorder
from .settings import Settings
from .state import StateStore

log = logging.getLogger(__name__)


def nvr_spec(n: Nvr) -> NvrSpec:
    return NvrSpec(id=n.id, name=n.name, host=n.host, http_port=n.http_port, rtsp_port=n.rtsp_port, https=n.https,
                   username=n.username, password=n.password, event_codes=tuple(n.event_codes or ("VideoMotion",)))


def camera_url(cam: Camera, nvr: Nvr | None) -> str | None:
    if cam.source_url:
        return cam.source_url
    if nvr is None:
        return None
    return nvr_spec(nvr).rtsp_url(cam.channel, cam.stream)


def camera_spec(cam: Camera, nvr: Nvr | None) -> CameraSpec | None:
    url = camera_url(cam, nvr)
    if url is None:
        return None
    # без регистратора событий движения нет — анализируем постоянно
    trigger = cam.trigger if (nvr is not None and not cam.source_url) else "always"
    return CameraSpec(
        id=cam.id, slug=cam.slug, name=cam.name, url=url, nvr_id=cam.nvr_id, channel=cam.channel,
        trigger=trigger, fps=cam.fps, linger=cam.linger, clear_after=cam.clear_after,
        species=tuple(cam.species or ("cat", "dog")),
        zone=tuple(tuple(p) for p in cam.zone) if cam.zone and len(cam.zone) >= 3 else None,
        direction=cam.direction or None, min_conf=cam.min_conf, confirm_hits=cam.confirm_hits,
        confirm_conf=cam.confirm_conf, identity_conf=cam.identity_conf, save_frames=cam.save_frames,
    )


class Runtime:
    def __init__(self, settings: Settings, engine: Engine, bus: EventBus):
        self.settings = settings
        self.engine = engine
        self.bus = bus
        self.state = StateStore(bus)
        self.recorder = Recorder(settings, engine)
        self.detector = None
        self.identifier = None
        self.detector_info: dict = {"status": "not_loaded"}
        self.identity_names: dict[int, str] = {}
        self._workers: dict[int, CameraWorker] = {}
        self._by_channel: dict[tuple[int, int], CameraWorker] = {}
        self._listeners: dict[int, DahuaEventListener] = {}
        self._nvr_codes: dict[int, tuple[str, ...]] = {}
        self._lock = threading.RLock()

    # --- жизненный цикл ---

    def start(self) -> None:
        self.reload_config()
        if self.settings.run_pipeline:
            threading.Thread(target=self.reload_models, name="model-loader", daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            self._stop_threads()

    def _stop_threads(self) -> None:
        for w in self._workers.values():
            w.stop()
        for lst in self._listeners.values():
            lst.stop_event.set()
        for w in self._workers.values():
            w.join(timeout=5)
        self._workers.clear()
        self._by_channel.clear()
        self._listeners.clear()

    # --- модели ---

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.settings.data_dir / p

    def reload_models(self) -> None:
        from .vision.detector import Detector, Identifier

        with Session(self.engine) as s:
            det_row = s.exec(select(MlModel).where(MlModel.kind == "detector", MlModel.active == True)).first()  # noqa: E712
            cls_row = s.exec(select(MlModel).where(MlModel.kind == "classifier", MlModel.active == True)).first()  # noqa: E712
        weights = self._resolve(det_row.path) if det_row else self.settings.models_dir / "base" / self.settings.base_detector
        weights.parent.mkdir(parents=True, exist_ok=True)
        self.detector_info = {"status": "loading", "weights": weights.name}
        try:
            det = Detector(weights, device=self.settings.device, fmt=self.settings.inference_format)
            det.warmup()
            self.detector = det
            self.detector_info = {"status": "ready", "weights": weights.name,
                                  "format": self.settings.inference_format, "model_id": det_row.id if det_row else None,
                                  "classes": [n for n in det.names.values() if n in ("cat", "dog")]}
        except Exception as e:  # noqa: BLE001
            log.exception("Не удалось загрузить детектор")
            self.detector_info = {"status": "error", "error": str(e)}
            return
        if cls_row:
            try:
                class_map = {k: int(v) for k, v in cls_row.classes.items() if v is not None}
                self.identifier = Identifier(self._resolve(cls_row.path), class_map, self.settings.device,
                                             fmt=self.settings.inference_format)
                self.detector_info["classifier_id"] = cls_row.id
            except Exception:  # noqa: BLE001
                log.exception("Не удалось загрузить классификатор")
                self.identifier = None
        else:
            self.identifier = None

    def detector_status(self) -> dict:
        info = dict(self.detector_info)
        if self.detector is not None and self.detector.avg_ms is not None:
            info["avg_ms"] = round(self.detector.avg_ms, 1)
        return info

    # --- конфигурация ---

    def reload_config(self) -> None:
        with self._lock:
            self._stop_threads()
            with Session(self.engine) as s:
                nvrs = {n.id: n for n in s.exec(select(Nvr)).all()}
                cams = s.exec(select(Camera)).all()
                idents = s.exec(select(Identity)).all()
                last_dir = self._last_directions(s)
            self.identity_names = {i.id: i.name for i in idents}
            self.state.configure([(c.id, list(c.species or [])) for c in cams], [i.id for i in idents], last_dir)
            if not self.settings.run_pipeline:
                return
            motion_nvrs: set[int] = set()
            for cam in cams:
                nvr = nvrs.get(cam.nvr_id) if cam.nvr_id else None
                if not cam.enabled or (nvr is not None and not nvr.enabled):
                    continue
                spec = camera_spec(cam, nvr)
                if spec is None:
                    continue
                w = CameraWorker(spec, self)
                self._workers[cam.id] = w
                if spec.nvr_id is not None and spec.trigger == "motion":
                    self._by_channel[(spec.nvr_id, spec.channel)] = w
                    motion_nvrs.add(spec.nvr_id)
                w.start()
            for nvr_id in motion_nvrs:
                spec = nvr_spec(nvrs[nvr_id])
                self._nvr_codes[nvr_id] = spec.event_codes
                lst = DahuaEventListener(spec, self.on_dahua_event, self.on_dahua_disconnect)
                self._listeners[nvr_id] = lst
                lst.start()
            log.info("Запущено камер: %d, подписок на регистраторы: %d", len(self._workers), len(self._listeners))
        self.bus.publish({"type": "config_changed"})

    @staticmethod
    def _last_directions(s: Session) -> dict[int, dict]:
        """Восстановить «последнее направление» каждого животного после перезапуска."""
        out: dict[int, dict] = {}
        rows = s.exec(select(Event).where(col(Event.identity_id).is_not(None), col(Event.kind).in_(["arrived", "left"]))
                      .order_by(col(Event.ts).desc()).limit(500)).all()
        for ev in rows:
            if ev.identity_id not in out:
                out[ev.identity_id] = {"last_direction": ev.kind, "last_camera_id": ev.camera_id,
                                       "last_seen": ev.ts.timestamp()}
        return out

    # --- события регистраторов ---

    def on_dahua_event(self, nvr_id: int, ev: DahuaEvent) -> None:
        if ev.code not in self._nvr_codes.get(nvr_id, ()):
            return
        w = self._by_channel.get((nvr_id, ev.channel))
        if w is None:
            return
        if ev.action == "Start":
            w.on_motion(True)
        elif ev.action == "Stop":
            w.on_motion(False)
        elif ev.action == "Pulse":
            w.on_motion(True)
            w.on_motion(False)

    def on_dahua_disconnect(self, nvr_id: int) -> None:
        for (nid, _), w in list(self._by_channel.items()):
            if nid == nvr_id:
                w.on_motion(False)

    # --- доступ для API ---

    def latest_frame(self, camera_id: int) -> np.ndarray | None:
        w = self._workers.get(camera_id)
        if w is None:
            return None
        frame, _, _ = w.reader.latest()
        return frame

    def worker(self, camera_id: int) -> CameraWorker | None:
        return self._workers.get(camera_id)

    def listener_status(self) -> dict[int, bool]:
        return {nid: lst.connected for nid, lst in self._listeners.items()}
