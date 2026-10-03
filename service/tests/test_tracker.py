import pytest

from catdetect.vision.geometry import DirectionRule, iou, point_in_polygon
from catdetect.vision.tracker import Detection, HoldState, Tracker, TrackerParams


def box_at(x, y, s=0.1):
    return (x - s / 2, y - s / 2, x + s / 2, y + s / 2)


def test_iou_and_polygon():
    assert iou((0, 0, 1, 1), (0, 0, 1, 1)) == pytest.approx(1)
    assert iou((0, 0, 1, 1), (2, 2, 3, 3)) == 0
    square = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]
    assert point_in_polygon((0.5, 0.5), square)
    assert not point_in_polygon((0.1, 0.5), square)


def test_direction_rule_sides():
    # горизонтальная линия на y=0.5, дверь внизу кадра
    rule = DirectionRule([[0, 0.5], [1, 0.5]], [0.5, 0.9], margin=0.02)
    assert rule.side((0.5, 0.8)) == 1
    assert rule.side((0.5, 0.2)) == -1
    assert rule.side((0.5, 0.51)) is None
    with pytest.raises(ValueError):
        DirectionRule([[0, 0.5], [1, 0.5]], [0.3, 0.5])


def walk(tracker, ys, species="cat", conf=0.8, x=0.5, dt=0.2, t0=0.0):
    events = []
    for i, y in enumerate(ys):
        events += tracker.update([Detection(species, conf, box_at(x, y))], t0 + i * dt)
    return events


def test_confirmation_requires_hits_and_conf():
    tr = Tracker(TrackerParams(confirm_hits=3, confirm_conf=0.5))
    ev = walk(tr, [0.3, 0.31, 0.32], conf=0.3)
    assert ev == []
    tr = Tracker(TrackerParams(confirm_hits=3, confirm_conf=0.5))
    ev = walk(tr, [0.3, 0.31, 0.32])
    assert [e.kind for e in ev] == ["seen"]
    assert len(tr.tracks) == 1


def test_arrived_and_left():
    rule = DirectionRule([[0, 0.5], [1, 0.5]], [0.5, 0.9])
    tr = Tracker(TrackerParams(confirm_hits=2), rule)
    ev = walk(tr, [0.2, 0.28, 0.36, 0.44, 0.52, 0.6, 0.68])
    assert [e.kind for e in ev] == ["seen", "arrived"]
    t = ev[-1].track
    assert t.side == 1
    ev = walk(tr, [0.6, 0.5, 0.42, 0.34], t0=1.4)  # тот же трек идёт обратно
    assert [e.kind for e in ev] == ["left"]


def test_crossing_before_confirmation_is_reported():
    rule = DirectionRule([[0, 0.5], [1, 0.5]], [0.5, 0.9])
    tr = Tracker(TrackerParams(confirm_hits=4, confirm_conf=0.5), rule)
    ev = walk(tr, [0.42, 0.5, 0.58, 0.66])
    assert [e.kind for e in ev] == ["seen", "arrived"]


def test_track_continuity_with_jumps_and_expiry():
    tr = Tracker(TrackerParams(max_distance=0.15, max_age=1.0))
    tr.update([Detection("cat", 0.9, box_at(0.3, 0.3))], 0)
    tr.update([Detection("cat", 0.9, box_at(0.42, 0.3))], 0.2)  # без пересечения боксов, но рядом
    assert len(tr.tracks) == 1
    tr.update([], 2.0)
    assert len(tr.tracks) == 0


def test_two_animals_and_species_vote():
    tr = Tracker(TrackerParams(confirm_hits=2))
    for i in range(3):
        tr.update([
            Detection("cat" if i != 1 else "dog", 0.7, box_at(0.2, 0.5)),
            Detection("dog", 0.9, box_at(0.8, 0.5)),
        ], i * 0.2)
    species = sorted(t.species for t in tr.tracks.values())
    assert species == ["cat", "dog"]


def test_identity_vote():
    tr = Tracker()
    walk(tr, [0.3, 0.3, 0.3])
    t = next(iter(tr.tracks.values()))
    t.add_identity(7, 0.9)
    t.add_identity(7, 0.8)
    t.add_identity(3, 0.6)
    ident, score = t.identity(0.5)
    assert ident == 7 and score == pytest.approx(1.7 / 3)
    assert t.identity(0.9)[0] is None


def test_hold_state():
    h = HoldState(5)
    assert h.update(True, 0) and h.state
    assert not h.update(False, 3) and h.state
    assert h.update(False, 6) and not h.state


def test_apply_aspect():
    import numpy as np

    from catdetect.nvr.reader import apply_aspect, parse_aspect

    frame = np.zeros((1616, 1440, 3), np.uint8)
    assert apply_aspect(frame, None) is frame
    assert apply_aspect(frame, "16:9").shape[:2] == (810, 1440)  # шире — уменьшаем высоту
    assert apply_aspect(frame, "3:4").shape[:2] == (1616, 1212)  # уже — уменьшаем ширину
    assert parse_aspect("4:3") == pytest.approx(4 / 3)
    assert parse_aspect("bad") is None and parse_aspect("0:3") is None
