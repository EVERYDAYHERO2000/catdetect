"""Хранение состояния сервиса и приём push-обновлений по WebSocket."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CatDetectClient, CatDetectError
from .const import DOMAIN, SIGNAL_EVENT, SIGNAL_SNAPSHOT

_LOGGER = logging.getLogger(__name__)


def _structure(data: dict[str, Any]) -> tuple:
    """Набор сущностей зависит только от этих полей; при их изменении интеграция перезагружается."""
    cams = tuple(sorted((c["id"], tuple(c["species"]), c["has_direction"]) for c in data.get("cameras", [])))
    idents = tuple(sorted(i["id"] for i in data.get("identities", [])))
    return cams, idents


class CatDetectCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: CatDetectClient) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self.client = client
        self.connected = False
        self._ws_task: asyncio.Task | None = None
        self._structure: tuple | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.client.get_state()
        except CatDetectError as err:
            raise UpdateFailed(str(err)) from err
        structure = _structure(data)
        if self._structure is not None and structure != self._structure and self.config_entry:
            _LOGGER.info("CatDetect: изменился набор камер/объектов — перезагрузка интеграции")
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        self._structure = structure
        return data

    # --- индексы ---

    def camera(self, camera_id: int) -> dict[str, Any] | None:
        return next((c for c in (self.data or {}).get("cameras", []) if c["id"] == camera_id), None)

    def identity(self, identity_id: int) -> dict[str, Any] | None:
        return next((i for i in (self.data or {}).get("identities", []) if i["id"] == identity_id), None)

    # --- WebSocket ---

    def start(self) -> None:
        self._ws_task = self.config_entry.async_create_background_task(
            self.hass, self.client.listen(self._on_message, self._on_connect, self._on_disconnect), f"{DOMAIN}_ws"
        )

    async def stop(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()

    async def _on_connect(self) -> None:
        self.connected = True
        # после переподключения — полное состояние, чтобы не пропустить изменения
        await self.async_refresh()

    def _on_disconnect(self) -> None:
        if self.connected:
            self.connected = False
            self.async_update_listeners()

    def _on_message(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        data = self.data
        if data is None:
            return
        if kind == "camera_state":
            cam = self.camera(msg["camera_id"])
            if cam is not None:
                cam["state"] = msg["state"]
                self.async_set_updated_data(data)
        elif kind == "identity_state":
            ident = self.identity(msg["identity_id"])
            if ident is not None:
                ident["state"] = msg["state"]
                self.async_set_updated_data(data)
        elif kind == "event":
            ev = msg["event"]
            async_dispatcher_send(self.hass, SIGNAL_EVENT.format(self.config_entry.entry_id), ev)
        elif kind == "snapshot":
            async_dispatcher_send(self.hass, SIGNAL_SNAPSHOT.format(self.config_entry.entry_id), msg["camera_id"])
        elif kind == "config_changed":
            self.hass.async_create_task(self.async_request_refresh())
