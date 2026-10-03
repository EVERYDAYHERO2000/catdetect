"""Обработка одной камеры: движение → кадры → детекция → трекер → события/состояния."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from .annotate import draw_overlay, to_jpeg
from .nvr.reader import StreamReader
from .vision.detector import crop
from .vision.geometry import DirectionRule, center, point_in_polygon
from .vision.tracker import Detection, HoldState, Track, TrackEvent, Tracker, TrackerParams

if TYPE_CHECKING:
    from .runtime import Runtime

log = logging.getLogger(__name__)

MAX_IDENTITY_SAMPLES = 10  # сколько кропов трека классифицировать
MAX_SAVED_FRAMES_PER_TRACK = 5
SAVE_FRAME_INTERVAL = 2.0  # сек между сохранёнными кадрами одного трека
STALE_FRAME = 5.0  # сек — кадр старше считается потерянным потоком

_TRANSLIT = {k: v.strip("_") for k, v in zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    "a b v g d e e zh z i y k l m n o p r s t u f kh ts ch sh sch _ y _ e yu ya".split(),
)}


def ascii_label(text: str) -> str:
    return "".join(_TRANSLIT.get(c, _TRANSLIT.get(c.lower(), c)) if not c.isascii() else c for c in text)


@dataclass(frozen=True)
class CameraSpec:
    id: int
    slug: str
    name: str
    url: str
    nvr_id: int | None
    channel: int
    trigger: str
    fps: float
    linger: float
    clear_after: float
    species: tuple[str, ...]
    zone: tuple[tuple[float, float], ...] | None
    direction: dict[str, Any] | None
    min_conf: float
    confirm_hits: int
    confirm_conf: float
    identity_conf: float
    save_frames: bool


class CameraWorker(threading.Thread):
    def __init__(self, spec: CameraSpec, runtime: Runtime):
        super().__init__(name=f"camera-{spec.slug}", daemon=True)
        self.spec = spec
        self.rt = runtime
        self.stop_event = threading.Event()
        self.reader = StreamReader(spec.url, spec.slug, self.stop_event)
        self.direction = DirectionRule.from_config(spec.direction)
        self.tracker = Tracker(
            TrackerParams(confirm_hits=spec.confirm_hits, confirm_conf=spec.confirm_conf), self.direction
        )
        self._motion = False
        self._motion_until = 0.0
        self._lock = threading.Lock()
        self._present = {sp: HoldState(spec.clear_after) for sp in spec.species}
        self._at_door = {sp: HoldState(spec.clear_after) for sp in spec.species}
        self._id_present: dict[int, HoldState] = {}
        self._id_at_door: dict[int, HoldState] = {}

    # --- управление ---

    def stop(self) -> None:
        self.stop_event.set()

    def on_motion(self, active: bool) -> None:
        with self._lock:
            if active:
                self._motion = True
            else:
                self._motion = False
                self._motion_until = time.monotonic() + self.spec.linger
        self.rt.state.set_flag(self.spec.id, "motion", active)

    def _active(self, now: float) -> bool:
        if self.spec.trigger == "always" or self.tracker.tracks:
            return True
        with self._lock:
            return self._motion or now < self._motion_until

    # --- основной цикл ---

    def run(self) -> None:
        self.reader.start()
        interval = 1.0 / max(self.spec.fps, 0.1)
        last_id = 0
        next_t = 0.0
        while not self.stop_event.is_set():
            now = time.monotonic()
            self.rt.state.set_flag(self.spec.id, "online", self.reader.connected)
            if not self._active(now):
                self._update_presence(now)
                self.stop_event.wait(0.2)
                continue
            if now < next_t:
                self.stop_event.wait(next_t - now)
                continue
            next_t = now + interval
            frame, fid, fts = self.reader.latest()
            if frame is None or fid == last_id or now - fts > STALE_FRAME:
                self._update_presence(now)
                continue
            last_id = fid
            try:
                self.process(frame, now)
            except Exception:  # noqa: BLE001
                log.exception("Камера %s: ошибка обработки кадра", self.spec.slug)
                self.stop_event.wait(1.0)
        self.reader.join(timeout=5)

    def filter_zone(self, dets: list[Detection]) -> list[Detection]:
        if not self.spec.zone:
            return dets
        return [d for d in dets if point_in_polygon(center(d.box), self.spec.zone)]

    def process(self, frame: np.ndarray, now: float) -> list[TrackEvent]:
        detector = self.rt.detector
        if detector is None:
            return []
        dets = self.filter_zone(detector.detect(frame, self.spec.species, self.spec.min_conf))
        events = self.tracker.update(dets, now)

        identifier = self.rt.identifier
        if identifier is not None:
            for t in self.tracker.tracks.values():
                if t.last_seen == now and t.identity_samples < MAX_IDENTITY_SAMPLES:
                    t.add_identity(*identifier.identify(crop(frame, t.box)))

        for ev in events:
            self._emit(ev, frame)
        self._maybe_save_frame(frame, now)
        self._update_presence(now)
        return events

    # --- вспомогательное ---

    def _boxes(self, now: float | None = None) -> list[dict[str, Any]]:
        out = []
        for t in self.tracker.tracks.values():
            if now is not None and t.last_seen != now:
                continue
            ident, _ = t.identity(self.spec.identity_conf)
            name = self.rt.identity_names.get(ident, "") if ident else ""
            label = f"#{t.id} {ascii_label(name) or t.species} {t.mean_conf:.2f}"
            out.append({"box": t.box, "species": t.species, "label": label, "confirmed": t.confirmed,
                        "conf": round(t.mean_conf, 3), "identity_id": ident, "track_id": t.id})
        return out

    def _emit(self, ev: TrackEvent, frame: np.ndarray) -> None:
        t: Track = ev.track
        ident, iconf = t.identity(self.spec.identity_conf)
        img = draw_overlay(frame, self.spec.zone and [list(p) for p in self.spec.zone], self.spec.direction,
                           self._boxes())
        snapshot = to_jpeg(img)
        record = self.rt.recorder.save_event(self.spec.id, ev.kind, t.species, ident, t.mean_conf, iconf, t.id,
                                             snapshot)
        log.info("Камера %s: %s %s (трек %d, conf %.2f, объект %s)", self.spec.slug, t.species, ev.kind, t.id,
                 t.mean_conf, self.rt.identity_names.get(ident, "?") if ident else "-")
        self.rt.state.on_event(record, snapshot)

    def _maybe_save_frame(self, frame: np.ndarray, now: float) -> None:
        if not self.spec.save_frames:
            return
        due = [t for t in self.tracker.tracks.values()
               if t.confirmed and t.last_seen == now and t.saved_frames < MAX_SAVED_FRAMES_PER_TRACK
               and now - t.last_saved_at >= SAVE_FRAME_INTERVAL]
        if not due:
            return
        preds = [{k: b[k] for k in ("box", "species", "conf", "identity_id")} for b in self._boxes(now)]
        self.rt.recorder.save_frame(self.spec.id, frame, preds)
        for t in due:
            t.saved_frames += 1
            t.last_saved_at = now

    def _update_presence(self, now: float) -> None:
        confirmed = self.tracker.confirmed_tracks()
        for sp in self.spec.species:
            present = any(t.species == sp for t in confirmed)
            at_door = any(t.species == sp and t.side == 1 for t in confirmed)
            c1 = self._present[sp].update(present, now)
            c2 = self._at_door[sp].update(at_door, now)
            if c1 or c2:
                self.rt.state.set_species(self.spec.id, sp, self._present[sp].state, self._at_door[sp].state)

        seen: dict[int, bool] = {}
        for t in confirmed:
            ident, _ = t.identity(self.spec.identity_conf)
            if ident is not None:
                seen[ident] = seen.get(ident, False) or t.side == 1
        for ident in set(seen) | set(self._id_present):
            hp = self._id_present.setdefault(ident, HoldState(self.spec.clear_after))
            hd = self._id_at_door.setdefault(ident, HoldState(self.spec.clear_after))
            c1 = hp.update(ident in seen, now)
            c2 = hd.update(seen.get(ident, False), now)
            if c1 or c2:
                self.rt.state.set_identity_presence(self.spec.id, ident, hp.state, hd.state)
