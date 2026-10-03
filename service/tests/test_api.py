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
    out, cmap = export_classify(app.state.engine, s.data_dir, s.datasets_dir / "c")
    assert set(cmap.values()) == {cat1["id"], cat2["id"]}
    assert list((out / "val").iterdir())
    try:
        export_detect(app.state.engine, s.data_dir, s.datasets_dir / "x", min_images=100)
        raise AssertionError
    except DatasetError:
        pass
    crop = authed.get(f"/api/identities/crops/{detail['annotations'][0]['id']}")
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
