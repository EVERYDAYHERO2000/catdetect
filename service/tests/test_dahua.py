from catdetect.nvr.dahua import NvrSpec, parse_event_line, parse_kv


def test_parse_motion_start():
    ev = parse_event_line("Code=VideoMotion;action=Start;index=2")
    assert ev.code == "VideoMotion" and ev.action == "Start" and ev.channel == 3


def test_parse_with_data_containing_semicolons():
    ev = parse_event_line('Code=SmartMotionHuman;action=Stop;index=0;data={"a": "x;y", "b": 1}')
    assert ev.channel == 1 and ev.data == {"a": "x;y", "b": 1}


def test_ignore_non_event_lines():
    assert parse_event_line("--myboundary") is None
    assert parse_event_line("Heartbeat") is None
    assert parse_event_line("Content-Length: 39") is None


def test_rtsp_url_escapes_password():
    n = NvrSpec(id=1, name="n", host="10.0.0.5", username="admin", password="p@ss:w/rd")
    assert n.rtsp_url(4, "sub") == "rtsp://admin:p%40ss%3Aw%2Frd@10.0.0.5:554/cam/realmonitor?channel=4&subtype=1"
    assert "subtype=0" in n.rtsp_url(1, "main")


def test_parse_kv():
    assert parse_kv("deviceType=NVR4108\nserialNumber=ABC\n") == {"deviceType": "NVR4108", "serialNumber": "ABC"}


import socket
import threading
import time

import pytest
import uvicorn

from catdetect.nvr.dahua import DahuaEventListener, get_device_info, get_snapshot


@pytest.fixture(scope="module")
def fake_nvr():
    from tests.fake_nvr import app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    yield NvrSpec(id=7, name="fake", host="127.0.0.1", http_port=port)
    server.should_exit = True


def test_device_info_and_snapshot(fake_nvr):
    info = get_device_info(fake_nvr)
    assert info["device_type"] == "DHI-NVR-FAKE"
    assert info["channels"][0] == {"channel": 1, "name": "Вход"}
    assert len(info["channels"]) == 4
    jpg = get_snapshot(fake_nvr, 2)
    assert jpg and jpg[:2] == b"\xff\xd8"
    assert get_snapshot(fake_nvr, 99) is None


def test_event_listener_receives_motion(fake_nvr):
    got = []
    stop = threading.Event()
    lst = DahuaEventListener(fake_nvr, lambda nid, ev: got.append((nid, ev.code, ev.action, ev.channel)),
                             stop_event=stop)
    lst.start()
    deadline = time.time() + 5
    while len(got) < 2 and time.time() < deadline:
        time.sleep(0.05)
    stop.set()
    assert got == [(7, "VideoMotion", "Start", 1), (7, "VideoMotion", "Stop", 1)]
