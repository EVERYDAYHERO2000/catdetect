"""Простой трекер для небольшого числа животных в кадре.

Сопоставление детекций с треками: сначала по IoU, затем по расстоянию между центрами
(при низком fps бокс кота может «прыгнуть»). Вид (cat/dog) и личность определяются
голосованием по всем кадрам трека — одиночные ошибки модели не меняют результат.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .geometry import Box, DirectionRule, Point, center, distance, iou


@dataclass(frozen=True)
class Detection:
    species: str
    conf: float
    box: Box


@dataclass
class Track:
    id: int
    box: Box
    first_seen: float
    last_seen: float
    hits: int = 0
    species_scores: dict[str, float] = field(default_factory=dict)
    recent_conf: deque = field(default_factory=lambda: deque(maxlen=10))
    max_conf: float = 0.0
    confirmed: bool = False
    side: int | None = None  # +1 — сторона двери, -1 — внешняя
    # голосование личности: identity_id -> сумма вероятностей; None — «не узнан»
    identity_scores: dict[int | None, float] = field(default_factory=dict)
    identity_samples: int = 0
    saved_frames: int = 0
    last_saved_at: float = float("-inf")

    @property
    def species(self) -> str:
        return max(self.species_scores, key=self.species_scores.get)

    @property
    def mean_conf(self) -> float:
        return sum(self.recent_conf) / len(self.recent_conf) if self.recent_conf else 0.0

    @property
    def position(self) -> Point:
        return center(self.box)

    def add(self, det: Detection, ts: float) -> None:
        self.box = det.box
        self.last_seen = ts
        self.hits += 1
        self.species_scores[det.species] = self.species_scores.get(det.species, 0.0) + det.conf
        self.recent_conf.append(det.conf)
        self.max_conf = max(self.max_conf, det.conf)

    def add_identity(self, identity_id: int | None, prob: float) -> None:
        self.identity_scores[identity_id] = self.identity_scores.get(identity_id, 0.0) + prob
        self.identity_samples += 1

    def identity(self, threshold: float) -> tuple[int | None, float | None]:
        """Лучшая личность и её средняя вероятность; None, если ниже порога."""
        if not self.identity_samples:
            return None, None
        best = max(self.identity_scores, key=self.identity_scores.get)
        score = self.identity_scores[best] / self.identity_samples
        if best is None or score < threshold:
            return None, score
        return best, score


@dataclass(frozen=True)
class TrackEvent:
    kind: str  # seen | arrived | left
    track: Track


@dataclass
class TrackerParams:
    iou_threshold: float = 0.2
    max_distance: float = 0.15
    max_age: float = 3.0  # сек без детекций до удаления трека
    confirm_hits: int = 3
    confirm_conf: float = 0.5


class Tracker:
    def __init__(self, params: TrackerParams | None = None, direction: DirectionRule | None = None):
        self.params = params or TrackerParams()
        self.direction = direction
        self.tracks: dict[int, Track] = {}
        self._next_id = 1

    def reset(self) -> None:
        self.tracks.clear()

    def _match(self, dets: list[Detection]) -> tuple[dict[int, int], list[int]]:
        p = self.params
        assigned: dict[int, int] = {}  # track_id -> det index
        used: set[int] = set()

        pairs = sorted(
            ((iou(t.box, d.box), tid, di) for tid, t in self.tracks.items() for di, d in enumerate(dets)),
            reverse=True,
        )
        for score, tid, di in pairs:
            if score < p.iou_threshold:
                break
            if tid in assigned or di in used:
                continue
            assigned[tid] = di
            used.add(di)

        pairs = sorted(
            (distance(t.position, center(d.box)), tid, di)
            for tid, t in self.tracks.items() if tid not in assigned
            for di, d in enumerate(dets) if di not in used
        )
        for dist, tid, di in pairs:
            if dist > p.max_distance:
                break
            if tid in assigned or di in used:
                continue
            assigned[tid] = di
            used.add(di)

        return assigned, [i for i in range(len(dets)) if i not in used]

    def update(self, dets: list[Detection], ts: float) -> list[TrackEvent]:
        p = self.params
        assigned, unmatched = self._match(dets)
        updated: list[Track] = []
        for tid, di in assigned.items():
            t = self.tracks[tid]
            t.add(dets[di], ts)
            updated.append(t)
        for di in unmatched:
            t = Track(id=self._next_id, box=dets[di].box, first_seen=ts, last_seen=ts)
            self._next_id += 1
            t.add(dets[di], ts)
            self.tracks[t.id] = t
            updated.append(t)

        events: list[TrackEvent] = []
        for t in updated:
            if not t.confirmed and t.hits >= p.confirm_hits and t.mean_conf >= p.confirm_conf:
                t.confirmed = True
                events.append(TrackEvent("seen", t))
            if self.direction is not None:
                s = self.direction.side(t.position)
                if s is None:
                    pass
                elif t.side is None:
                    t.side = s
                elif s != t.side and t.confirmed:
                    # пересечение до подтверждения тоже засчитается — сторона не обновлялась
                    events.append(TrackEvent("arrived" if s == 1 else "left", t))
                    t.side = s

        for tid in [tid for tid, t in self.tracks.items() if ts - t.last_seen > p.max_age]:
            del self.tracks[tid]
        return events

    def confirmed_tracks(self) -> list[Track]:
        return [t for t in self.tracks.values() if t.confirmed]


class HoldState:
    """Булево состояние с удержанием: гаснет только через hold секунд после последнего True."""

    def __init__(self, hold: float):
        self.hold = hold
        self.state = False
        self._last_true = float("-inf")

    def update(self, value: bool, now: float) -> bool:
        """Возвращает True, если состояние изменилось."""
        if value:
            self._last_true = now
            new = True
        else:
            new = self.state and (now - self._last_true) < self.hold
        changed = new != self.state
        self.state = new
        return changed
