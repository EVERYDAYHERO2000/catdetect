"""Эмулятор HTTP API регистратора Dahua для тестов и разработки интерфейса.

Запуск: cd service && python -m uvicorn tests.fake_nvr:app --port 8081
"""

from __future__ import annotations

import asyncio

import cv2
import numpy as np
from fastapi import FastAPI, Response
from fastapi.responses import PlainTextResponse, StreamingResponse

CHANNELS = ["Вход", "Двор", "Калитка", "Гараж"]
COLORS = [(60, 90, 140), (60, 130, 70), (130, 90, 60), (90, 90, 90)]

app = FastAPI()


@app.get("/cgi-bin/magicBox.cgi")
def magic_box(action: str):
    return PlainTextResponse("deviceType=DHI-NVR-FAKE\nserialNumber=FAKE123456\n")


@app.get("/cgi-bin/configManager.cgi")
def config_manager(action: str, name: str):
    if name != "ChannelTitle":
        return PlainTextResponse("Error", status_code=400)
    return PlainTextResponse("".join(f"table.ChannelTitle[{i}].Name={t}\n" for i, t in enumerate(CHANNELS)))


@app.get("/cgi-bin/devVideoInput.cgi")
def video_inputs(action: str):
    return PlainTextResponse(f"result={len(CHANNELS)}\n")


@app.get("/cgi-bin/snapshot.cgi")
def snapshot(channel: int = 1):
    if not 1 <= channel <= len(CHANNELS):
        return Response(status_code=400)
    img = np.full((360, 640, 3), COLORS[channel - 1], dtype=np.uint8)
    cv2.rectangle(img, (220, 120), (420, 300), (200, 200, 200), -1)
    cv2.putText(img, f"CH {channel}", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
    ok, buf = cv2.imencode(".jpg", img)
    return Response(buf.tobytes(), media_type="image/jpeg")


@app.get("/cgi-bin/eventManager.cgi")
async def events(action: str, codes: str = "", heartbeat: int = 20):
    async def stream():
        boundary = "--myboundary\r\nContent-Type: text/plain\r\nContent-Length: {n}\r\n\r\n{body}\r\n"
        for action_ in ("Start", "Stop"):
            body = f"Code=VideoMotion;action={action_};index=0"
            yield boundary.format(n=len(body), body=body).encode()
            await asyncio.sleep(0.2)
        while True:  # heartbeat, как у настоящего регистратора
            yield boundary.format(n=9, body="Heartbeat").encode()
            await asyncio.sleep(heartbeat)

    return StreamingResponse(stream(), media_type="multipart/x-mixed-replace; boundary=myboundary")
