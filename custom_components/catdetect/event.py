"""События: «кошка пришла к двери», «Барсик ушёл» и т.п. — удобные триггеры автоматизаций."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CatDetectConfigEntry
from .const import EVENT_KINDS, SIGNAL_EVENT
from .entity import CameraEntity, IdentityEntity


async def async_setup_entry(hass: HomeAssistant, entry: CatDetectConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coord = entry.runtime_data
    entities: list[EventEntity] = [CameraEvents(coord, cam) for cam in coord.data["cameras"]]
    entities += [IdentityEvents(coord, ident) for ident in coord.data["identities"]]
    async_add_entities(entities)


class _EventMixin(EventEntity):
    def _attrs(self, ev: dict[str, Any]) -> dict[str, Any]:
        coord = self.coordinator  # type: ignore[attr-defined]
        ident = coord.identity(ev["identity_id"]) if ev.get("identity_id") else None
        cam = coord.camera(ev["camera_id"])
        return {
            "species": ev["species"],
            "identity_id": ev.get("identity_id"),
            "identity": ident["name"] if ident else None,
            "camera_id": ev["camera_id"],
            "camera": cam["name"] if cam else None,
            "confidence": ev.get("confidence"),
            "identity_confidence": ev.get("identity_confidence"),
            "event_id": ev.get("id"),
        }

    def _matches(self, ev: dict[str, Any]) -> bool:
        raise NotImplementedError

    def _event_type(self, ev: dict[str, Any]) -> str:
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        entry_id = self.coordinator.config_entry.entry_id  # type: ignore[attr-defined]
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_EVENT.format(entry_id), self._on_event))

    @callback
    def _on_event(self, ev: dict[str, Any]) -> None:
        if not self._matches(ev):
            return
        event_type = self._event_type(ev)
        if event_type in self.event_types:
            self._trigger_event(event_type, self._attrs(ev))
            self.async_write_ha_state()


class CameraEvents(CameraEntity, _EventMixin):
    """Типы событий: cat_seen, cat_arrived, cat_left, dog_…"""

    _attr_translation_key = "camera_events"

    def __init__(self, coord, cam: dict[str, Any]) -> None:
        super().__init__(coord, cam, "events")
        self._attr_event_types = [f"{sp}_{kind}" for sp in cam["species"] for kind in EVENT_KINDS]

    def _matches(self, ev: dict[str, Any]) -> bool:
        return ev["camera_id"] == self.camera_id

    def _event_type(self, ev: dict[str, Any]) -> str:
        return f"{ev['species']}_{ev['kind']}"


class IdentityEvents(IdentityEntity, _EventMixin):
    """Типы событий: seen, arrived, left — по любой камере."""

    _attr_translation_key = "identity_events"
    _attr_event_types = list(EVENT_KINDS)

    def __init__(self, coord, ident: dict[str, Any]) -> None:
        super().__init__(coord, ident, "events")

    def _matches(self, ev: dict[str, Any]) -> bool:
        return ev.get("identity_id") == self.identity_id

    def _event_type(self, ev: dict[str, Any]) -> str:
        return ev["kind"]
