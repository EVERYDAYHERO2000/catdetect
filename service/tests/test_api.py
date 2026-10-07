import numpy as np

from catdetect.annotate import to_jpeg


def test_setup_login_flow(client):
    assert client.get("/api/auth/status").json()["setup_required"] is True
    assert client.get("/api/cameras").status_code == 401
    client.post("/api/auth/setup", json={"username": "admin", "password": "password123"})
    assert client.post("/api/auth/setup", json={"username": "x", "password": "password123"}).status_code == 409
    assert client.get("/api/cameras").status_code == 200
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.get("/api/cameras").status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "password123"}).status_code == 200
    assert client.get("/api/auth/status").json()["authenticated"] is True


def test_api_token(authed):
    tok = authed.post("/api/auth/tokens", json={"name": "Home Assistant"}).json()["token"]
    authed.cookies.clear()
    h = {"Authorization": f"Bearer {tok}"}
    assert authed.get("/api/state", headers=h).status_code == 200
    assert authed.get("/api/auth/tokens", headers=h).status_code == 403  # токены — только по паролю
    assert authed.get("/api/state", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_nvr_and_camera_crud(authed):
    n = authed.post("/api/nvrs", json={"name": "NVR1", "host": "192.168.1.50", "password": "secret"}).json()
    assert "password" not in n and n["has_password"] is True
    cam = {"slug": "front_door", "name": "Входная дверь", "nvr_id": n["id"], "channel": 3,
           "zone": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]],
           "direction": {"line": [[0, 0.5], [1, 0.5]], "door_point": [0.5, 0.9]}}
    r = authed.post("/api/cameras", json=cam)
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["direction"]["door_point"] == [0.5, 0.9]
    assert authed.post("/api/cameras", json=cam).status_code == 409
    bad = {**cam, "slug": "x2", "direction": {"line": [[0, 0.5], [1, 0.5]], "door_point": [0.3, 0.5]}}
    assert authed.post("/api/cameras", json=bad).status_code == 422
    assert authed.post("/api/cameras", json={**cam, "slug": "Bad Slug"}).status_code == 422
    assert authed.delete(f"/api/nvrs/{n['id']}").status_code == 409
    # обновление без пароля не стирает его
    authed.put(f"/api/nvrs/{n['id']}", json={"name": "NVR1b", "host": "192.168.1.50"})
    assert authed.get("/api/nvrs").json()[0]["has_password"] is True
    state = authed.get("/api/state").json()
    assert state["cameras"][0]["slug"] == "front_door"
    assert state["cameras"][0]["has_direction"] is True
    assert authed.delete(f"/api/cameras/{c['id']}").status_code == 200


def test_labeling_and_dataset_export(authed, app):
    from catdetect.dataset import DatasetError, export_classify, export_detect

    cat1 = authed.post("/api/identities", json={"name": "Барсик", "species": "cat"}).json()
    cat2 = authed.post("/api/identities", json={"name": "Чужие", "species": "cat", "is_own": False}).json()
    img = to_jpeg(np.full((120, 160, 3), 127, dtype=np.uint8))
    ids = authed.post("/api/images/upload", files=[("files", (f"{i}.jpg", img, "image/jpeg")) for i in range(12)]).json()["ids"]
    assert len(ids) == 12
    for n, iid in enumerate(ids):
        ident = cat1["id"] if n % 2 else cat2["id"]
        r = authed.put(f"/api/images/{iid}/annotations",
                       json={"annotations": [{"box": [0.1, 0.1, 0.5, 0.6], "species": "cat", "identity_id": ident}]})
        assert r.status_code == 200
    detail = authed.get(f"/api/images/{ids[0]}").json()
    assert detail["status"] == "labeled" and len(detail["annotations"]) == 1
    stats = authed.get("/api/images/stats").json()
    assert stats["by_status"]["labeled"] == 12

    s = app.state.settings
    data_yaml = export_detect(app.state.engine, s.data_dir, s.datasets_dir / "d")
    assert data_yaml.exists()
    labels = list((s.datasets_dir / "d" / "labels").rglob("*.txt"))
    assert labels and labels[0].read_text().startswith("0 ")
    import yaml
    assert yaml.safe_load(data_yaml.read_text())["names"] == {0: "cat", 1: "dog", 2: "person"}
    person = authed.post("/api/identities", json={"name": "Илья", "species": "person"}).json()
    r = authed.put(f"/api/images/{ids[0]}/annotations",
                   json={"annotations": [{"box": [0.1, 0.1, 0.5, 0.9], "species": "person", "identity_id": person["id"]}]})
    assert r.status_code == 200 and r.json()["annotations"][0]["species"] == "person"
    out, cmap = export_classify(app.state.engine, s.data_dir, s.datasets_dir / "c")
    assert set(cmap.values()) == {cat1["id"], cat2["id"]}
    assert list((out / "val").iterdir())
    try:
        export_detect(app.state.engine, s.data_dir, s.datasets_dir / "x", min_images=100)
        raise AssertionError
    except DatasetError:
        pass
    crop = authed.get(f"/api/identities/crops/{r.json()['annotations'][0]['id']}")
    assert crop.headers["content-type"] == "image/jpeg"


def test_websocket_requires_auth(client):
    from starlette.websockets import WebSocketDisconnect
    try:
        with client.websocket_connect("/api/ws") as ws:
            ws.receive_text()
        raise AssertionError("should be rejected")
    except WebSocketDisconnect as e:
        assert e.code == 4401


def test_websocket_receives_events(authed, app):
    with authed.websocket_connect("/api/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        app.state.bus.publish({"type": "config_changed"})
        assert ws.receive_json() == {"type": "config_changed"}


def test_compute_preference(authed, monkeypatch):
    from catdetect import compute

    r = authed.get("/api/system/compute").json()
    assert r["preference"] == "auto" and r["available"]["cpu"] is True
    assert authed.put("/api/system/compute", json={"preference": "cpu"}).status_code == 200
    assert authed.get("/api/system/compute").json()["preference"] == "cpu"
    assert authed.put("/api/system/compute", json={"preference": "tpu"}).status_code == 422

    monkeypatch.setattr(compute, "detect_gpu", lambda: ("cuda:0", "RTX"))
    assert compute.resolve("auto").device == "cuda:0"
    assert compute.resolve("cpu", "torch").device == "cpu"
    monkeypatch.setattr(compute, "detect_gpu", lambda: None)
    c = compute.resolve("gpu", "torch")
    assert c.kind == "cpu" and c.fmt == "torch"


def test_event_frame_to_labeling(authed, app):
    import numpy as np

    n = authed.post("/api/nvrs", json={"name": "N", "host": "127.0.0.1", "password": "x"}).json()
    cam = authed.post("/api/cameras", json={"slug": "door", "name": "Дверь", "nvr_id": n["id"], "channel": 1}).json()
    rec = app.state.runtime.recorder
    frame = np.full((120, 160, 3), 90, np.uint8)
    pred = [{"box": [0.1, 0.2, 0.4, 0.8], "species": "person", "conf": 0.7, "identity_id": None}]
    ev = rec.save_event(cam["id"], "seen", "person", None, 0.7, None, 1, b"\xff\xd8fake", None, raw=frame, predictions=pred)
    assert ev["has_raw"] is True and ev["image_id"] is None

    r = authed.post(f"/api/events/{ev['id']}/label").json()
    img = authed.get(f"/api/images/{r['image_id']}").json()
    assert img["source"] == "event" and img["status"] == "unlabeled" and img["camera_id"] == cam["id"]
    assert img["predictions"][0]["species"] == "person"
    # повторно — тот же кадр, без дублей
    assert authed.post(f"/api/events/{ev['id']}/label").json()["image_id"] == r["image_id"]
    # кадр удалили из разметки — можно отправить снова
    assert authed.delete(f"/api/images/{r['image_id']}").status_code == 200
    assert authed.post(f"/api/events/{ev['id']}/label").json()["image_id"] != r["image_id"]
    # событие без исходного кадра
    ev2 = rec.save_event(cam["id"], "seen", "cat", None, 0.7, None, 1, None)
    assert authed.post(f"/api/events/{ev2['id']}/label").status_code == 404


def test_objects_folders_flow(authed, app):
    import numpy as np

    from catdetect.dataset import export_detect

    rec = app.state.runtime.recorder
    barsik = authed.post("/api/identities", json={"name": "Барсик", "species": "cat"}).json()
    frame = np.full((120, 160, 3), 100, np.uint8)
    preds = [
        {"box": [0.1, 0.1, 0.3, 0.4], "species": "cat", "conf": 0.8, "identity_id": barsik["id"]},
        {"box": [0.6, 0.6, 0.9, 0.9], "species": "cat", "conf": 0.4, "identity_id": None},
    ]
    image_ids = [rec.save_frame(None, frame, preds) for _ in range(12)]
    s = authed.get("/api/objects/summary").json()
    assert s["pending"] == 24 and s["folders"][0]["count"] == 0

    crops = authed.get("/api/objects/crops?folder=pending&limit=500").json()
    assert crops["total"] == 24
    first = [c for c in crops["items"] if c["suggested_identity_id"] == barsik["id"]]
    second = [c for c in crops["items"] if c["suggested_identity_id"] is None]
    # подсказки классификатора принимаются одной кнопкой
    assert authed.post("/api/objects/accept_suggestions", json={"ids": [c["id"] for c in first]}).json()["moved"] == 12
    # вторая рамка — коврик: «Не объект»
    assert authed.post("/api/objects/move", json={"ids": [c["id"] for c in second], "target": "rejected"}).json()["moved"] == 12

    s = authed.get("/api/objects/summary").json()
    assert s["pending"] == 0 and s["rejected"] == 12 and s["folders"][0]["count"] == 12
    assert authed.get(f"/api/images/{image_ids[0]}").json()["status"] == "labeled"
    # в редакторе кадра «Не объект» не показывается
    assert len(authed.get(f"/api/images/{image_ids[0]}").json()["annotations"]) == 1

    st = app.state.settings
    data = export_detect(app.state.engine, st.data_dir, st.datasets_dir / "objects")
    lines = [p.read_text() for p in (st.datasets_dir / "objects" / "labels").rglob("*.txt")]
    assert all(t.count("\n") == 0 and t.startswith("0 ") for t in lines)  # одна рамка на кадр, без «коврика»
    assert data.exists()

    # перекладка обратно и удаление папки возвращают снимки в «Неразобранные»
    authed.delete(f"/api/identities/{barsik['id']}")
    s = authed.get("/api/objects/summary").json()
    assert s["pending"] == 12 and s["folders"] == []
    assert authed.get(f"/api/images/{image_ids[0]}").json()["status"] == "unlabeled"
    assert authed.post("/api/objects/move", json={"ids": [1], "target": 999}).status_code == 404


def test_camera_aspect_validation(authed):
    n = authed.post("/api/nvrs", json={"name": "N", "host": "127.0.0.1"}).json()
    base = {"slug": "a1", "name": "A", "nvr_id": n["id"], "channel": 1}
    assert authed.post("/api/cameras", json={**base, "aspect": "16:9"}).json()["aspect"] == "16:9"
    assert authed.post("/api/cameras", json={**base, "slug": "a2", "aspect": ""}).json()["aspect"] is None
    assert authed.post("/api/cameras", json={**base, "slug": "a3", "aspect": "4/3"}).json()["aspect"] == "4:3"
    assert authed.post("/api/cameras", json={**base, "slug": "a4", "aspect": "wide"}).status_code == 422


def test_images_follow_camera_aspect(authed, app):
    import cv2
    import numpy as np

    from catdetect.annotate import to_jpeg

    n = authed.post("/api/nvrs", json={"name": "N", "host": "127.0.0.1"}).json()
    cam = authed.post("/api/cameras", json={"slug": "door", "name": "Д", "nvr_id": n["id"], "channel": 1}).json()
    rec = app.state.runtime.recorder
    portrait = np.zeros((1616, 1440, 3), np.uint8)  # снято до выбора пропорций
    ev = rec.save_event(cam["id"], "seen", "cat", None, 0.9, None, 1, to_jpeg(portrait), None, raw=portrait,
                        predictions=[{"box": [0.1, 0.1, 0.5, 0.5], "species": "cat", "conf": 0.9, "identity_id": None}])
    shape = lambda r: cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR).shape[:2]
    assert shape(authed.get(f"/api/events/{ev['id']}/image")) == (1616, 1440)  # пропорции не заданы — как есть

    authed.put(f"/api/cameras/{cam['id']}", json={**{k: v for k, v in cam.items() if k != "id"}, "aspect": "4:3"})
    assert shape(authed.get(f"/api/events/{ev['id']}/image")) == (1080, 1440)
    image_id = authed.post(f"/api/events/{ev['id']}/label").json()["image_id"]
    assert shape(authed.get(f"/api/images/{image_id}/file")) == (1080, 1440)
    crop_id = authed.get("/api/objects/crops?folder=pending").json()["items"][0]["id"]
    h, w = shape(authed.get(f"/api/identities/crops/{crop_id}"))
    assert abs(w / h - (0.4 * 1440) / (0.4 * 1080)) < 0.05  # вырезка тоже в пропорциях камеры


def test_overview(authed):
    r = authed.get("/api/overview")
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"cameras", "nvrs", "objects", "events", "training", "system"}
    assert d["system"]["mem_total"] > 0 and 0 <= d["system"]["cpu_percent"] <= 100
    assert d["cameras"]["total"] == 0 and d["training"]["jobs"] == 0


def test_retention_keeps_only_sorted(authed, app):
    from datetime import timedelta

    import numpy as np
    from sqlmodel import Session, select

    from catdetect.annotate import to_jpeg
    from catdetect.cleanup import DEFAULT_HOURS, run_cleanup
    from catdetect.models import Annotation, Event, Image, utcnow

    assert DEFAULT_HOURS == 48
    n = authed.post("/api/nvrs", json={"name": "N", "host": "127.0.0.1"}).json()
    cam = authed.post("/api/cameras", json={"slug": "door", "name": "Д", "nvr_id": n["id"], "channel": 1}).json()
    barsik = authed.post("/api/identities", json={"name": "Барсик", "species": "cat"}).json()
    rec, st, eng = app.state.runtime.recorder, app.state.settings, app.state.engine
    frame = np.zeros((60, 80, 3), np.uint8)
    jpg = to_jpeg(frame)
    one = [{"box": [0.1, 0.1, 0.4, 0.4], "species": "cat", "conf": 0.9, "identity_id": None}]
    two = one + [{"box": [0.6, 0.6, 0.9, 0.9], "species": "cat", "conf": 0.8, "identity_id": None}]

    ev_old = rec.save_event(cam["id"], "seen", "cat", None, 0.9, None, 1, jpg, None, raw=frame, predictions=one)
    ev_new = rec.save_event(cam["id"], "seen", "cat", None, 0.9, None, 1, jpg, None, raw=frame, predictions=one)
    f_pending = rec.save_frame(cam["id"], frame, one)        # только неразобранная карточка
    f_mixed = rec.save_frame(cam["id"], frame, two)          # одна разложена, другая нет
    f_sorted = rec.save_frame(cam["id"], frame, one)         # разложена
    f_rejected = rec.save_frame(cam["id"], frame, one)       # «Не объект»
    f_negative = rec.save_frame(cam["id"], frame, [])        # «Нет объектов»
    f_empty = rec.save_frame(cam["id"], frame, [])           # никого не нашли, не размечен
    f_pending_new = rec.save_frame(cam["id"], frame, one)    # свежий неразобранный

    crops = {c["image_id"]: c for c in reversed(authed.get("/api/objects/crops?folder=pending&limit=500").json()["items"])}
    mixed_ids = [c["id"] for c in authed.get("/api/objects/crops?folder=pending&limit=500").json()["items"] if c["image_id"] == f_mixed]
    authed.post("/api/objects/move", json={"ids": [mixed_ids[0], crops[f_sorted]["id"]], "target": barsik["id"]})
    authed.post("/api/objects/move", json={"ids": [crops[f_rejected]["id"]], "target": "rejected"})
    authed.put(f"/api/images/{f_negative}/annotations", json={"annotations": []})

    old = utcnow() - timedelta(hours=60)
    with Session(eng) as s:
        e = s.get(Event, ev_old["id"]); e.ts = old; s.add(e)
        old_event_files = [st.data_dir / e.snapshot_path, st.data_dir / e.raw_path]
        for iid in (f_pending, f_mixed, f_sorted, f_rejected, f_negative, f_empty):
            im = s.get(Image, iid); im.captured_at = old; s.add(im)
        s.commit()

    res = run_cleanup(eng, st.data_dir, 48)
    assert res == {"events": 1, "crops": 2, "images": 2, "hours": 48}
    assert not any(p.exists() for p in old_event_files)
    with Session(eng) as s:
        assert s.get(Event, ev_old["id"]) is None and s.get(Event, ev_new["id"]) is not None
        assert s.get(Image, f_pending) is None and s.get(Image, f_empty) is None
        for keep in (f_mixed, f_sorted, f_rejected, f_negative, f_pending_new):
            assert s.get(Image, keep) is not None, keep
        mixed_left = s.exec(select(Annotation).where(Annotation.image_id == f_mixed)).all()
        assert [a.state for a in mixed_left] == ["assigned"] and s.get(Image, f_mixed).status == "labeled"
    assert run_cleanup(eng, st.data_dir, 48)["crops"] == 0

    assert authed.get("/api/system/retention").json()["hours"] == 48
    assert authed.put("/api/system/retention", json={"hours": 72}).json()["hours"] == 72
    assert authed.put("/api/system/retention", json={"hours": -1}).status_code == 422



def test_suggestions_must_match_species(authed, app):
    import numpy as np
    from sqlmodel import Session

    from catdetect.labels import normalize_legacy
    from catdetect.models import Annotation

    ilia = authed.post("/api/identities", json={"name": "Илья", "species": "person"}).json()
    rex = authed.post("/api/identities", json={"name": "Рекс", "species": "dog"}).json()
    rec = app.state.runtime.recorder
    frame = np.zeros((60, 80, 3), np.uint8)
    rec.save_frame(None, frame, [
        {"box": [0.1, 0.1, 0.4, 0.4], "species": "dog", "conf": 0.9, "identity_id": ilia["id"]},  # собака «Илья»
        {"box": [0.5, 0.5, 0.9, 0.9], "species": "dog", "conf": 0.9, "identity_id": rex["id"]},
    ])
    crops = authed.get("/api/objects/crops?folder=pending").json()["items"]
    sugg = sorted(c["suggested_identity_id"] for c in crops if c["suggested_identity_id"])
    assert sugg == [rex["id"]]  # подсказку другого вида не сохранили

    # старая ошибочная подсказка в базе: принять нельзя, при запуске очищается
    with Session(app.state.engine) as s:
        a = s.get(Annotation, crops[0]["id"] if crops[0]["suggested_identity_id"] is None else crops[1]["id"])
        a.suggested_identity_id = ilia["id"]; s.add(a); s.commit(); bad_id = a.id
    r = authed.post("/api/objects/accept_suggestions", json={"ids": [bad_id]}).json()
    assert r["moved"] == 0
    normalize_legacy(app.state.engine)
    with Session(app.state.engine) as s:
        assert s.get(Annotation, bad_id).suggested_identity_id is None



def test_annotation_species_must_match_identity(authed, app):
    import numpy as np

    ilia = authed.post("/api/identities", json={"name": "Илья", "species": "person"}).json()
    image_id = app.state.runtime.recorder.save_frame(None, np.zeros((60, 80, 3), np.uint8), [], "upload")
    bad = {"annotations": [{"box": [0.1, 0.1, 0.5, 0.5], "species": "dog", "identity_id": ilia["id"]}]}
    r = authed.put(f"/api/images/{image_id}/annotations", json=bad)
    assert r.status_code == 422 and "Илья" in r.json()["detail"]
    ok = {"annotations": [{"box": [0.1, 0.1, 0.5, 0.5], "species": "person", "identity_id": ilia["id"]}]}
    assert authed.put(f"/api/images/{image_id}/annotations", json=ok).status_code == 200


def test_one_identity_per_frame_in_folders(authed, app):
    import numpy as np

    ilia = authed.post("/api/identities", json={"name": "Илья", "species": "person"}).json()
    rec = app.state.runtime.recorder
    frame = np.zeros((60, 80, 3), np.uint8)
    two = [{"box": [0.1, 0.1, 0.4, 0.4], "species": "person", "conf": 0.9, "identity_id": ilia["id"], "identity_conf": 0.8},
           {"box": [0.5, 0.5, 0.9, 0.9], "species": "person", "conf": 0.7, "identity_id": ilia["id"], "identity_conf": 0.9}]
    img1 = rec.save_frame(None, frame, two)
    img2 = rec.save_frame(None, frame, two[:1])
    crops = authed.get("/api/objects/crops?folder=pending&limit=50").json()["items"]
    on1 = [c for c in crops if c["image_id"] == img1]
    assert sum(1 for c in on1 if c["suggested_identity_id"] == ilia["id"]) == 1  # подсказка «Илья» одна на кадр

    r = authed.post("/api/objects/move", json={"ids": [c["id"] for c in crops], "target": ilia["id"]}).json()
    assert r == {"moved": 2, "skipped": 1}  # с кадра 1 — только одна карточка, с кадра 2 — своя
    left = authed.get("/api/objects/crops?folder=pending").json()["items"]
    assert [c["image_id"] for c in left] == [img1]

    dup = {"annotations": [{"box": [0.1, 0.1, 0.4, 0.4], "species": "person", "identity_id": ilia["id"]},
                           {"box": [0.5, 0.5, 0.9, 0.9], "species": "person", "identity_id": ilia["id"]}]}
    r = authed.put(f"/api/images/{img2}/annotations", json=dup)
    assert r.status_code == 422 and "дважды" in r.json()["detail"]


def test_dedupe_assigned(authed, app):
    import numpy as np
    from sqlmodel import Session, select

    from catdetect.maintenance import dedupe_assigned
    from catdetect.models import Annotation

    mask = authed.post("/api/identities", json={"name": "Маск", "species": "cat"}).json()
    rec, eng = app.state.runtime.recorder, app.state.engine
    frame = np.zeros((60, 80, 3), np.uint8)
    img_dup = rec.save_frame(None, frame, [{"box": [0.1, 0.1, 0.4, 0.4], "species": "cat", "conf": 0.9},
                                           {"box": [0.11, 0.11, 0.41, 0.41], "species": "cat", "conf": 0.5}])
    img_two = rec.save_frame(None, frame, [{"box": [0.1, 0.1, 0.3, 0.3], "species": "cat", "conf": 0.9},
                                           {"box": [0.6, 0.6, 0.9, 0.9], "species": "cat", "conf": 0.5}])
    with Session(eng) as s:  # как в старых данных: обе карточки кадра в одной папке
        for a in s.exec(select(Annotation)).all():
            a.state, a.identity_id = "assigned", mask["id"]; s.add(a)
        s.commit()
    dry = dedupe_assigned(eng)
    assert dry == {"frames": 2, "removed_duplicate_boxes": 1, "returned_to_pending": 1, "applied": False}
    assert dedupe_assigned(eng, apply=True)["applied"]
    with Session(eng) as s:
        dup = s.exec(select(Annotation).where(Annotation.image_id == img_dup)).all()
        two = s.exec(select(Annotation).where(Annotation.image_id == img_two)).all()
    assert len(dup) == 1 and dup[0].conf == 0.9
    assert sorted(a.state for a in two) == ["assigned", "pending"]
    assert dedupe_assigned(eng)["frames"] == 0
