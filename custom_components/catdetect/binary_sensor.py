from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CatDetectConfigEntry
from .entity import CameraEntity, IdentityEntity


async def async_setup_entry(hass: HomeAssistant, entry: CatDetectConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coord = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for cam in coord.data["cameras"]:
        for sp in cam["species"]:
            entities.append(SpeciesSensor(coord, cam, sp, at_door=False))
            if cam["has_direction"]:
                entities.append(SpeciesSensor(coord, cam, sp, at_door=True))
        entities.append(CameraFlag(coord, cam, "motion"))
        entities.append(CameraFlag(coord, cam, "online"))
    for ident in coord.data["identities"]:
        entities.append(IdentitySensor(coord, ident, "present"))
        entities.append(IdentitySensor(coord, ident, "at_door"))
    async_add_entities(entities)


class SpeciesSensor(CameraEntity, BinarySensorEntity):
    """Кошка/собака в кадре (или у двери — на стороне двери от линии направления)."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, coord, cam: dict[str, Any], species: str, at_door: bool) -> None:
        key = f"{species}_at_door" if at_door else f"{species}_present"
        super().__init__(coord, cam, key)
        self._species = species
        self._field = "at_door" if at_door else "present"
        self._attr_translation_key = key

    @property
    def is_on(self) -> bool:
        return bool(self.cam_state.get("species", {}).get(self._species, {}).get(self._field))


class CameraFlag(CameraEntity, BinarySensorEntity):
    def __init__(self, coord, cam: dict[str, Any], flag: str) -> None:
        super().__init__(coord, cam, flag)
        self._flag = flag
        self._attr_translation_key = flag
        if flag == "motion":
            self._attr_device_class = BinarySensorDeviceClass.MOTION
        else:
            self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool:
        return bool(self.cam_state.get(self._flag))


class IdentitySensor(IdentityEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, coord, ident: dict[str, Any], field: str) -> None:
        super().__init__(coord, ident, field)
        self._field = field
        self._attr_translation_key = f"identity_{field}"

    @property
    def is_on(self) -> bool:
        return bool(self.ident_state.get(self._field))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"camera": self.camera_name(self.ident_state.get("last_camera_id"))}
