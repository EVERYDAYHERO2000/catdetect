import numpy as np

from catdetect.pipeline import CameraSpec, CameraWorker
from catdetect.vision.tracker import Detection


class Stub:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def f(*a, **kw):
            self.calls.append((name, a, kw))
            return {"id": len(self.calls), "camera_id": 1, "kind": a[1] if len(a) > 1 else None}
        return f


class FakeDetector:
    def __init__(self, dets):
        self.dets = dets

    def detect(self, frame, species, min_conf):
        return [d for d in self.dets if d.conf >= min_conf]


class FakeRuntime:
    def __init__(self, dets):
        self.detector = FakeDetector(dets)
        self.identifier = None
        self.identity_names = {}
        self.identity_species = {}
        self.recorder = Stub()
        self.state = Stub()


def spec(**kw):
    base = dict(id=1, slug="door", name="Door", url="/nonexistent.mp4", nvr_id=1, channel=2, trigger="motion",
                fps=5, linger=0, clear_after=5, species=("cat", "person"),
                zone=((0.0, 0.0), (0.4, 0.0), (0.4, 1.0), (0.0, 1.0)), direction=None, min_conf=0.25,
                confirm_hits=2, confirm_conf=0.5, identity_conf=0.6, save_frames=False)
    base.update(kw)
    return CameraSpec(**base)


def motion_events(rt):
    return [c for c in rt.recorder.calls if c[0] == "save_event" and c[1][1] == "motion"]


def test_motion_episode_explains_out_of_zone():
    rt = FakeRuntime([Detection("person", 0.3, (0.41, 0.44, 0.48, 0.63))])  # как на реальной камере
    w = CameraWorker(spec(), rt)
    frame = np.zeros((288, 352, 3), np.uint8)
    w.on_motion(True)
    for i in range(3):
        assert w.process(frame, float(i)) == []
    w.on_motion(False)
    w._finish_session()
    (_, args, kw), = motion_events(rt)
    details = kw.get("details") or args[8]
    assert details["frames"] == 3 and details["events"] == 0
    assert details["best"] == {"species": "person", "conf": 0.3, "reason": "out_of_zone"}
    assert args[7] is not None  # снимок


def test_motion_episode_with_detection_event():
    rt = FakeRuntime([Detection("person", 0.8, (0.1, 0.4, 0.2, 0.6))])
    w = CameraWorker(spec(), rt)
    frame = np.zeros((288, 352, 3), np.uint8)
    w.on_motion(True)
    events = [e.kind for i in range(3) for e in w.process(frame, float(i))]
    assert events == ["seen"]
    w._finish_session()
    (_, args, kw), = motion_events(rt)
    details = kw.get("details") or args[8]
    assert details["events"] == 1 and details["best"]["reason"] == "ok"
    # повторное движение без новой сессии не создаёт дубль
    w._finish_session()
    assert len(motion_events(rt)) == 1


class FakeIdentifier:
    def __init__(self, ident):
        self.ident = ident

    def identify(self, crop_img):
        return self.ident, 0.9


def test_identity_species_must_match_track():
    rt = FakeRuntime([Detection("cat", 0.8, (0.1, 0.4, 0.2, 0.6))])
    rt.identity_species = {5: "person", 6: "cat"}
    w = CameraWorker(spec(), rt)
    frame = np.zeros((288, 352, 3), np.uint8)
    rt.identifier = FakeIdentifier(5)  # классификатор ошибочно назвал кошку человеком
    w.process(frame, 0.0)
    t = next(iter(w.tracker.tracks.values()))
    assert t.identity(0.5)[0] is None
    rt.identifier = FakeIdentifier(6)
    for i in range(1, 4):
        w.process(frame, float(i))
    assert t.identity(0.5)[0] == 6


def test_stream_opened_only_on_motion(monkeypatch):
    import catdetect.pipeline as pl

    opened = []

    class FakeReader:
        def __init__(self, url, name, stop, aspect):
            self.stop, self.connected, self.native_size = stop, True, (640, 480)
            opened.append(self)

        def start(self):
            pass

        def latest(self):
            return None, 0, 0.0

    monkeypatch.setattr(pl, "StreamReader", FakeReader)
    rt = FakeRuntime([])
    w = CameraWorker(spec(keep_stream=False), rt)
    assert not w.keep_stream
    w._manage_stream(0.0, active=False)
    assert w.reader is None and opened == []  # без движения поток не открыт
    w._manage_stream(1.0, active=True)
    assert w.reader is not None and len(opened) == 1
    w._manage_stream(2.0, active=False)  # движение кончилось — ждём простоя
    assert w.reader is not None
    w._manage_stream(2.0 + pl.STREAM_IDLE_CLOSE + 1, active=False)
    assert w.reader is None and opened[0].stop.is_set()
    assert w._stream_online(100.0) is True  # закрыт намеренно — не «нет потока»

    always = CameraWorker(spec(), rt)  # по умолчанию поток держится всегда
    always._manage_stream(0.0, active=False)
    assert always.reader is not None
