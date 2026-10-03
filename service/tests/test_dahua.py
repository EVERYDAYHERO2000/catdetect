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
