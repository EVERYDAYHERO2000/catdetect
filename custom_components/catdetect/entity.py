"""Базовые классы сущностей: устройство на камеру и устройство на животное."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CatDetectCoordinator


class CatDetectEntity(CoordinatorEntity[CatDetectCoordinator]):
    _attr_has_entity_name = True

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.connected


class CameraEntity(CatDetectEntity):
    def __init__(self, coordinator: CatDetectCoordinator, camera: dict[str, Any], key: str) -> None:
        super().__init__(coordinator)
        self.camera_id = camera["id"]
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_camera_{self.camera_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_camera_{self.camera_id}")},
            name=camera["name"],
            manufacturer="CatDetect",
            model="Камера",
            configuration_url=f"{coordinator.client.url}/cameras/{self.camera_id}",
        )

    @property
    def cam_state(self) -> dict[str, Any]:
        cam = self.coordinator.camera(self.camera_id)
        return (cam or {}).get("state") or {}


class IdentityEntity(CatDetectEntity):
    def __init__(self, coordinator: CatDetectCoordinator, identity: dict[str, Any], key: str) -> None:
        super().__init__(coordinator)
        self.identity_id = identity["id"]
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_identity_{self.identity_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_identity_{self.identity_id}")},
            name=identity["name"],
            manufacturer="CatDetect",
            model=("Кошка" if identity["species"] == "cat" else "Собака") + ("" if identity["is_own"] else " (чужие)"),
            configuration_url=f"{coordinator.client.url}/identities",
        )

    @property
    def ident_state(self) -> dict[str, Any]:
        ident = self.coordinator.identity(self.identity_id)
        return (ident or {}).get("state") or {}

    def camera_name(self, camera_id: int | None) -> str | None:
        cam = self.coordinator.camera(camera_id) if camera_id is not None else None
        return cam["name"] if cam else None
