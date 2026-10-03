"""Работа с регистраторами Dahua по HTTP API (CGI) и RTSP."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NvrSpec:
    id: int
    name: str
    host: str
    http_port: int = 80
    rtsp_port: int = 554
    https: bool = False
    username: str = "admin"
    password: str = ""
    event_codes: tuple[str, ...] = ("VideoMotion",)

    @property
    def base_url(self) -> str:
        scheme = "https" if self.https else "http"
        default = 443 if self.https else 80
        port = "" if self.http_port == default else f":{self.http_port}"
        return f"{scheme}://{self.host}{port}"

    def rtsp_url(self, channel: int, stream: str = "sub") -> str:
        subtype = 0 if stream == "main" else 1
        cred = f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@"
        return f"rtsp://{cred}{self.host}:{self.rtsp_port}/cam/realmonitor?channel={channel}&subtype={subtype}"

    def client(self, **kw) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            auth=httpx.DigestAuth(self.username, self.password),
            verify=False,  # у регистраторов самоподписанные сертификаты
            **kw,
        )


@dataclass(frozen=True)
class DahuaEvent:
    code: str
    action: str  # Start | Stop | Pulse
    channel: int  # с 1, как в интерфейсе регистратора
    data: dict[str, Any] | None = None


def parse_event_line(line: str) -> DahuaEvent | None:
    """Разбор строки вида `Code=VideoMotion;action=Start;index=0[;data={...}]`."""
    line = line.strip()
    if not line.startswith("Code="):
        return None
    data = None
    idx = line.find(";data=")
    head = line
    if idx >= 0:
        head = line[:idx]
        try:
            data = json.loads(line[idx + len(";data="):])
        except ValueError:
            data = None
    fields: dict[str, str] = {}
    for part in head.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            fields[k.strip()] = v.strip()
    try:
        index = int(fields.get("index", "0"))
    except ValueError:
        index = 0
    return DahuaEvent(code=fields.get("Code", ""), action=fields.get("action", ""), channel=index + 1, data=data)


def parse_kv(text: str) -> dict[str, str]:
    """Ответы CGI в формате `key=value` по строкам."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def get_device_info(nvr: NvrSpec, timeout: float = 5.0) -> dict[str, Any]:
    """Проверка связи: тип устройства, серийный номер и названия каналов."""
    with nvr.client(timeout=timeout) as c:
        r = c.get("/cgi-bin/magicBox.cgi", params={"action": "getSystemInfo"})
        r.raise_for_status()
        info = parse_kv(r.text)
        channels: dict[int, str] = {}
        try:
            r = c.get("/cgi-bin/configManager.cgi", params={"action": "getConfig", "name": "ChannelTitle"})
            if r.status_code == 200:
                # table.ChannelTitle[0].Name=Вход
                for k, v in parse_kv(r.text).items():
                    if k.startswith("table.ChannelTitle[") and k.endswith("].Name"):
                        channels[int(k[len("table.ChannelTitle["):k.index("]")]) + 1] = v
        except (httpx.HTTPError, ValueError):
            pass
    return {
        "device_type": info.get("deviceType") or info.get("updateSerial") or "",
        "serial": info.get("serialNumber", ""),
        "channels": [{"channel": ch, "name": name} for ch, name in sorted(channels.items())],
    }


class DahuaEventListener(threading.Thread):
    """Подписка на поток событий регистратора (multipart, держится открытым).

    on_event(nvr_id, event) вызывается из этого потока; on_disconnect(nvr_id) — при обрыве,
    чтобы сбросить «залипшее» движение (Stop мог потеряться).
    """

    def __init__(
        self,
        nvr: NvrSpec,
        on_event: Callable[[int, DahuaEvent], None],
        on_disconnect: Callable[[int], None] | None = None,
        stop_event: threading.Event | None = None,
    ):
        super().__init__(name=f"dahua-events-{nvr.id}", daemon=True)
        self.nvr = nvr
        self.on_event = on_event
        self.on_disconnect = on_disconnect
        self.stop_event = stop_event or threading.Event()
        self.connected = False

    def _url(self) -> str:
        codes = ",".join(self.nvr.event_codes) or "All"
        return f"/cgi-bin/eventManager.cgi?action=attach&codes=[{codes}]&heartbeat=20"

    def run(self) -> None:
        backoff = 2.0
        while not self.stop_event.is_set():
            try:
                timeout = httpx.Timeout(10.0, read=60.0)
                with self.nvr.client(timeout=timeout) as c, c.stream("GET", self._url()) as r:
                    r.raise_for_status()
                    self.connected = True
                    backoff = 2.0
                    log.info("NVR %s: подписка на события установлена", self.nvr.name)
                    for line in r.iter_lines():
                        if self.stop_event.is_set():
                            break
                        ev = parse_event_line(line)
                        if ev is not None:
                            self.on_event(self.nvr.id, ev)
            except Exception as e:  # noqa: BLE001 — любой сбой сети: переподключаемся
                if not self.stop_event.is_set():
                    log.warning("NVR %s: поток событий прервался: %s", self.nvr.name, e)
            finally:
                if self.connected and self.on_disconnect:
                    self.on_disconnect(self.nvr.id)
                self.connected = False
            self.stop_event.wait(backoff)
            backoff = min(backoff * 2, 60.0)
