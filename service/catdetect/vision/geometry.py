"""Геометрия в нормализованных координатах кадра (0..1)."""

from __future__ import annotations

import math
from collections.abc import Sequence

Point = tuple[float, float]
Box = tuple[float, float, float, float]  # x1, y1, x2, y2


def center(box: Box) -> Point:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def iou(a: Box, b: Box) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def distance(p: Point, q: Point) -> float:
    return math.hypot(p[0] - q[0], p[1] - q[1])


def point_in_polygon(p: Point, poly: Sequence[Sequence[float]]) -> bool:
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xin:
                inside = not inside
    return inside


def signed_distance_to_line(p: Point, a: Sequence[float], b: Sequence[float]) -> float:
    """Расстояние со знаком от точки до прямой ab (знак — сторона)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return 0.0
    return (dx * (p[1] - a[1]) - dy * (p[0] - a[0])) / length


class DirectionRule:
    """Линия, разделяющая кадр на сторону двери (+1) и внешнюю сторону (-1).

    Около линии (|d| < margin) сторона не определена — гистерезис против дрожания бокса.
    """

    def __init__(self, line: Sequence[Sequence[float]], door_point: Sequence[float], margin: float = 0.02):
        self.a = tuple(line[0])
        self.b = tuple(line[1])
        self.margin = margin
        d = signed_distance_to_line(tuple(door_point), self.a, self.b)
        if abs(d) < 1e-9:
            raise ValueError("Точка двери не должна лежать на линии направления")
        self.door_sign = 1 if d > 0 else -1

    @classmethod
    def from_config(cls, cfg: dict | None) -> DirectionRule | None:
        if not cfg or not cfg.get("line") or not cfg.get("door_point"):
            return None
        return cls(cfg["line"], cfg["door_point"], float(cfg.get("margin", 0.02)))

    def side(self, p: Point) -> int | None:
        d = signed_distance_to_line(p, self.a, self.b)
        if abs(d) < self.margin:
            return None
        return 1 if (d > 0) == (self.door_sign > 0) else -1
