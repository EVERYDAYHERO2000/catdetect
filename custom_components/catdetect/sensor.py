from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CatDetectConfigEntry
from .entity import IdentityEntity


async def async_setup_entry(hass: HomeAssistant, entry: CatDetectConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coord = entry.runtime_data
    entities: list[SensorEntity] = []
    for ident in coord.data["identities"]:
        entities += [LastDirection(coord, ident), LastSeen(coord, ident), LastCamera(coord, ident)]
    async_add_entities(entities)


class LastDirection(IdentityEntity, SensorEntity):
    """Последнее направление: пришёл к двери / ушёл. Главный сенсор для «кот вернулся домой»."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["arrived", "left", "unknown"]
    _attr_translation_key = "last_direction"

    def __init__(self, coord, ident: dict[str, Any]) -> None:
        super().__init__(coord, ident, "last_direction")

    @property
    def native_value(self) -> str:
        return self.ident_state.get("last_direction") or "unknown"


class LastSeen(IdentityEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "last_seen"

    def __init__(self, coord, ident: dict[str, Any]) -> None:
        super().__init__(coord, ident, "last_seen")

    @property
    def native_value(self) -> datetime | None:
        ts = self.ident_state.get("last_seen")
        return datetime.fromtimestamp(ts, timezone.utc) if ts else None


class LastCamera(IdentityEntity, SensorEntity):
    _attr_translation_key = "last_camera"

    def __init__(self, coord, ident: dict[str, Any]) -> None:
        super().__init__(coord, ident, "last_camera")

    @property
    def native_value(self) -> str | None:
        return self.camera_name(self.ident_state.get("last_camera_id"))
