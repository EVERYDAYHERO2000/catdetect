"""Обработка одной камеры: движение → кадры → детекция → трекер → события/состояния."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np

from .annotate import draw_overlay, to_jpeg
from .nvr.reader import StreamReader, grab_frame
from .vision.detector import crop
from .vision.geometry import DirectionRule, center, point_in_polygon
from .vision.tracker import Detection, HoldState, Track, TrackEvent, Tracker, TrackerParams, assign_identities

if TYPE_CHECKING:
    from .runtime import Runtime

log = logging.getLogger(__name__)

MAX_IDENTITY_SAMPLES = 10  # сколько кропов трека классифицировать
MAX_SAVED_FRAMES_PER_TRACK = 5
SAVE_FRAME_INTERVAL = 2.0  # сек между сохранёнными кадрами одного трека
STALE_FRAME = 5.0  # сек — кадр старше считается потерянным потоком
STREAM_IDLE_CLOSE = 10.0  # сек без анализа до закрытия потока в режиме «только при движении»
STREAM_OPEN_TIMEOUT = 15.0  # сек на открытие потока, после — «нет потока»
DEBUG_CONF = 0.1  # в живом просмотре показываем и слабые детекции — чтобы было видно, почему не сработало

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
    aspect: str | None = None
    keep_stream: bool = True  # False — RTSP открывается только при движении (меньше трафика)


class CameraWorker(threading.Thread):
    def __init__(self, spec: CameraSpec, runtime: Runtime):
        super().__init__(name=f"camera-{spec.slug}", daemon=True)
        self.spec = spec
        self.rt = runtime
        self.stop_event = threading.Event()
        # поток держим открытым всегда, если так настроено или анализ не зависит от движения
        self.keep_stream = spec.keep_stream or spec.trigger != "motion"
        self.reader: StreamReader | None = None
        self._reader_stop: threading.Event | None = None
        self._opened_at = 0.0
        self._idle_since: float | None = None
        self._stream_ok = True  # удалось ли подключиться в прошлый раз (пока поток закрыт намеренно)
        self._viewer_until = 0.0  # открыт живой просмотр — держим поток, но не анализируем
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
        # последний обработанный кадр и все сырые детекции — для живого просмотра
        self._debug: tuple[float, np.ndarray, list[Detection]] | None = None
        # текущий эпизод движения — по его окончании пишется событие motion с разбором
        self._session: dict[str, Any] | None = None

    # --- управление ---

    def stop(self) -> None:
        self.stop_event.set()
        if self._reader_stop is not None:
            self._reader_stop.set()

    # --- видеопоток ---

    def _open_stream(self) -> None:
        if self.reader is not None:
            return
        self._reader_stop = threading.Event()
        self.reader = StreamReader(self.spec.url, self.spec.slug, self._reader_stop, self.spec.aspect)
        self._opened_at = time.monotonic()
        self.reader.start()

    def _close_stream(self) -> None:
        if self.reader is None:
            return
        self._stream_ok = self.reader.native_size is not None
        self._reader_stop.set()
        self.reader = None
        log.info("Камера %s: поток закрыт до следующего движения", self.spec.slug)

    def _manage_stream(self, now: float, active: bool) -> None:
        if self.keep_stream:
            self._open_stream()
            return
        if active or now < self._viewer_until:
            self._idle_since = None
            self._open_stream()
        elif self.reader is not None:
            if self._idle_since is None:
                self._idle_since = now
            elif now - self._idle_since > STREAM_IDLE_CLOSE:
                self._close_stream()

    def _stream_online(self, now: float) -> bool:
        if self.reader is None:
            return self._stream_ok  # закрыт намеренно — показываем, удалось ли подключиться в прошлый раз
        if self.reader.connected:
            self._stream_ok = True
            return True
        if self.keep_stream or now - self._opened_at > STREAM_OPEN_TIMEOUT:
            return False
        return self._stream_ok  # поток ещё открывается

    def on_motion(self, active: bool) -> None:
        log.info("Камера %s: движение %s", self.spec.slug, "началось" if active else "закончилось")
        with self._lock:
            if active:
                if self._session is None:
                    self._session = {"start": time.time(), "frames": 0, "events": 0, "best": None}
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
        interval = 1.0 / max(self.spec.fps, 0.1)
        last_id = 0
        next_t = 0.0
        while not self.stop_event.is_set():
            now = time.monotonic()
            active = self._active(now)
            self._manage_stream(now, active)
            self.rt.state.set_flag(self.spec.id, "online", self._stream_online(now))
            if not active:
                if self._session is not None:
                    self._finish_session()
                self._update_presence(now)
                self.stop_event.wait(0.2)
                continue
            if now < next_t:
                self.stop_event.wait(next_t - now)
                continue
            next_t = now + interval
            if self.reader is None:
                continue
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
        if self.reader is not None:
            self.reader.join(timeout=5)

    def filter_zone(self, dets: list[Detection]) -> list[Detection]:
        if not self.spec.zone:
            return dets
        return [d for d in dets if point_in_polygon(center(d.box), self.spec.zone)]

    def process(self, frame: np.ndarray, now: float) -> list[TrackEvent]:
        detector = self.rt.detector
        if detector is None:
            return []
        raw = detector.detect(frame, self.spec.species, min(DEBUG_CONF, self.spec.min_conf))
        self._debug = (now, frame, raw)
        self._note_session(frame, raw)
        dets = self.filter_zone([d for d in raw if d.conf >= self.spec.min_conf])
        events = self.tracker.update(dets, now)

        identifier = self.rt.identifier
        if identifier is not None:
            for t in self.tracker.tracks.values():
                if t.last_seen == now and t.identity_samples < MAX_IDENTITY_SAMPLES:
                    # ответ классификатора записываем как есть; вид проверяется при решении (_identity_of),
                    # потому что вид трека может смениться после голосования по следующим кадрам
                    t.add_identity(*identifier.identify(crop(frame, t.box)))

        for ev in events:
            self._emit(ev, frame)
        self._maybe_save_frame(frame, now)
        self._update_presence(now)
        return events

    # --- журнал движения ---

    def _reason(self, d: Detection) -> str:
        if self.spec.zone and not point_in_polygon(center(d.box), self.spec.zone):
            return "out_of_zone"
        if d.conf < self.spec.min_conf:
            return "weak"
        if d.conf < self.spec.confirm_conf:
            return "below_confirm"
        return "ok"

    def _note_session(self, frame: np.ndarray, raw: list[Detection]) -> None:
        s = self._session
        if s is None:
            return
        s["frames"] += 1
        s.setdefault("frame", frame)
        for d in raw:
            if s["best"] is None or d.conf > s["best"]["conf"]:
                s["best"] = {"species": d.species, "conf": round(d.conf, 3), "reason": self._reason(d)}
                s["frame"], s["box"] = frame, d.box

    def _finish_session(self) -> None:
        with self._lock:
            s, self._session = self._session, None
        if s is None:
            return
        frame = s.get("frame")
        if frame is None:
            frame = self.reader.latest()[0] if self.reader is not None else None
        best = s["best"]
        snapshot = None
        if frame is not None:
            boxes = [{"box": s["box"], "species": best["species"] if best["reason"] == "ok" else "",
                      "label": f"{best['species']} {best['conf']:.2f} {best['reason']}"}] if best else []
            snapshot = to_jpeg(draw_overlay(frame, self.spec.zone and [list(p) for p in self.spec.zone],
                                            self.spec.direction, boxes))
        details = {"duration": round(time.time() - s["start"], 1), "frames": s["frames"], "events": s["events"],
                   "best": best}
        preds = [{"box": list(s["box"]), "species": best["species"], "conf": best["conf"], "identity_id": None}] \
            if best else []
        record = self.rt.recorder.save_event(self.spec.id, "motion", best["species"] if best else "", None,
                                             best["conf"] if best else 0.0, None, 0, snapshot, details,
                                             raw=frame, predictions=preds)
        log.info("Камера %s: эпизод движения %.0f с, кадров %d, событий %d, лучшая детекция: %s", self.spec.slug,
                 details["duration"], s["frames"], s["events"],
                 f"{best['species']} {best['conf']:.2f} ({best['reason']})" if best else "нет")
        self.rt.state.on_motion_event(record)

    def _identities(self) -> dict[int, tuple[int | None, float | None]]:
        """Имена всех объектов в кадре сразу: только папки того же вида, что и объект,
        и каждое имя — не больше одному объекту (два «Ильи» в кадре быть не может)."""
        species = self.rt.identity_species
        tracks = list(self.tracker.tracks.values())
        cands = {t.id: t.identity_candidates(lambda i, sp=t.species: species.get(i) == sp) for t in tracks}
        latest = max((t.last_seen for t in tracks), default=0.0)
        # объекты на текущем кадре получают имена раньше «потерянных» (ещё не удалённых) треков
        priority = {t.id: 0 if t.last_seen == latest else 1 for t in tracks}
        return assign_identities(cands, self.spec.identity_conf, priority)

    def _identity_of(self, t: Track) -> tuple[int | None, float | None]:
        return self._identities().get(t.id, (None, None))

    # --- живой просмотр ---

    def debug_image(self) -> np.ndarray | None:
        """Кадр со всеми детекциями: подтверждённые треки, слабые и отброшенные зоной рамки, статус."""
        now = time.monotonic()
        self._viewer_until = now + 5.0
        dbg = self._debug
        if dbg is None or now - dbg[0] > 1.5:  # анализ сейчас не идёт — считаем на лету
            frame = self.reader.latest()[0] if self.reader is not None else None
            if frame is None and self.reader is None:  # поток закрыт до движения — берём одиночный кадр
                frame = grab_frame(self.spec.url, 10.0, self.spec.aspect)
            detector = self.rt.detector
            if frame is None or detector is None:
                return None
            dbg = (now, frame, detector.detect(frame, self.spec.species, DEBUG_CONF))
            analysing = False
        else:
            analysing = True
        _, frame, raw = dbg
        boxes = []
        for d in raw:
            in_zone = not self.spec.zone or point_in_polygon(center(d.box), self.spec.zone)
            ok = in_zone and d.conf >= self.spec.min_conf
            note = "" if ok else (" out-of-zone" if not in_zone else " weak")
            boxes.append({"box": d.box, "species": d.species if ok else "", "confirmed": ok,
                          "label": f"{d.species} {d.conf:.2f}{note}"})
        boxes += [{**b, "label": b["label"] + " OK"} for b in self._boxes() if b["confirmed"]]
        img = draw_overlay(frame, self.spec.zone and [list(p) for p in self.spec.zone], self.spec.direction, boxes)
        with self._lock:
            motion = self._motion
        h = img.shape[0]
        status = f"motion: {'YES' if motion else 'no'} | analysis: {'running' if analysing else 'idle'} | " \
                 f"min_conf {self.spec.min_conf:.2f} confirm {self.spec.confirm_conf:.2f}"
        k = max(1.0, max(img.shape[:2]) / 640)
        for color, thick in (((0, 0, 0), round(3 * k)), ((255, 255, 255), max(1, round(k)))):
            cv2.putText(img, status, (round(8 * k), h - round(10 * k)), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * k, color,
                        thick, cv2.LINE_AA)
        return img

    # --- вспомогательное ---

    def _boxes(self, now: float | None = None) -> list[dict[str, Any]]:
        out = []
        for t in self.tracker.tracks.values():
            if now is not None and t.last_seen != now:
                continue
            ident, _ = self._identity_of(t)
            name = self.rt.identity_names.get(ident, "") if ident else ""
            label = f"#{t.id} {ascii_label(name) or t.species} {t.mean_conf:.2f}"
            out.append({"box": t.box, "species": t.species, "label": label, "confirmed": t.confirmed,
                        "conf": round(t.mean_conf, 3), "identity_id": ident, "track_id": t.id})
        return out

    def _emit(self, ev: TrackEvent, frame: np.ndarray) -> None:
        t: Track = ev.track
        ident, iconf = self._identity_of(t)
        img = draw_overlay(frame, self.spec.zone and [list(p) for p in self.spec.zone], self.spec.direction,
                           self._boxes())
        snapshot = to_jpeg(img)
        if self._session is not None:
            self._session["events"] += 1
        preds = [{"box": list(b["box"]), "species": b["species"], "conf": b["conf"], "identity_id": b["identity_id"]}
                 for b in self._boxes(t.last_seen)]
        record = self.rt.recorder.save_event(self.spec.id, ev.kind, t.species, ident, t.mean_conf, iconf, t.id,
                                             snapshot, raw=frame, predictions=preds)
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
            ident, _ = self._identity_of(t)
            if ident is not None:
                seen[ident] = seen.get(ident, False) or t.side == 1
        for ident in set(seen) | set(self._id_present):
            hp = self._id_present.setdefault(ident, HoldState(self.spec.clear_after))
            hd = self._id_at_door.setdefault(ident, HoldState(self.spec.clear_after))
            c1 = hp.update(ident in seen, now)
            c2 = hd.update(seen.get(ident, False), now)
            if c1 or c2:
                self.rt.state.set_identity_presence(self.spec.id, ident, hp.state, hd.state)
