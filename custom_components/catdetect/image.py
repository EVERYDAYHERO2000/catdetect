"""Снимок последнего события камеры (с рамками) — для уведомлений и дашборда."""

from __future__ import annotations

from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import CatDetectConfigEntry
from .const import SIGNAL_SNAPSHOT
from .entity import CameraEntity


async def async_setup_entry(hass: HomeAssistant, entry: CatDetectConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coord = entry.runtime_data
    async_add_entities([LastSnapshot(hass, coord, cam) for cam in coord.data["cameras"]])


class LastSnapshot(CameraEntity, ImageEntity):
    _attr_translation_key = "last_snapshot"
    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, coord, cam: dict[str, Any]) -> None:
        CameraEntity.__init__(self, coord, cam, "last_snapshot")
        ImageEntity.__init__(self, hass)
        self._cached: bytes | None = None
        last = (cam.get("state") or {}).get("last_event")
        self._attr_image_last_updated = dt_util.parse_datetime(last["ts"]) if last and last.get("ts") else None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        entry_id = self.coordinator.config_entry.entry_id
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_SNAPSHOT.format(entry_id), self._on_snapshot))

    @callback
    def _on_snapshot(self, camera_id: int) -> None:
        if camera_id != self.camera_id:
            return
        self._cached = None
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        if self._cached is None:
            self._cached = await self.coordinator.client.get_snapshot(self.camera_id)
        return self._cached
