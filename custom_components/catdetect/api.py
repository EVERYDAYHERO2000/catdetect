"""Клиент API сервиса CatDetect."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class CatDetectError(Exception):
    """Ошибка связи с сервисом."""


class CatDetectAuthError(CatDetectError):
    """Неверный токен."""


class CatDetectClient:
    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        self.url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _get(self, path: str) -> aiohttp.ClientResponse:
        try:
            resp = await self._session.get(
                f"{self.url}{path}", headers=self._headers, timeout=aiohttp.ClientTimeout(total=15)
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CatDetectError(str(err)) from err
        if resp.status == 401:
            raise CatDetectAuthError("Неверный токен")
        if resp.status >= 400:
            raise CatDetectError(f"HTTP {resp.status}")
        return resp

    async def get_state(self) -> dict[str, Any]:
        resp = await self._get("/api/state")
        return await resp.json()

    async def get_snapshot(self, camera_id: int) -> bytes | None:
        try:
            resp = await self._get(f"/api/cameras/{camera_id}/last_snapshot")
        except CatDetectError:
            return None
        return await resp.read()

    async def listen(
        self,
        on_message: Callable[[dict[str, Any]], None],
        on_connect: Callable[[], Awaitable[None]],
        on_disconnect: Callable[[], None],
    ) -> None:
        """Бесконечный цикл WebSocket с переподключением. Отменяется через task.cancel()."""
        ws_url = self.url.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + "/api/ws"
        delay = 2
        while True:
            try:
                async with self._session.ws_connect(ws_url, headers=self._headers, heartbeat=30) as ws:
                    _LOGGER.debug("CatDetect: WebSocket подключён")
                    delay = 2
                    await on_connect()
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                on_message(msg.json())
                            except Exception:  # noqa: BLE001
                                _LOGGER.exception("CatDetect: ошибка обработки сообщения")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("CatDetect: WebSocket недоступен: %s", err)
            on_disconnect()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
