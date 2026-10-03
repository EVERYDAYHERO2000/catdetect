"""Интеграция CatDetect: животные на камерах Dahua."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CatDetectAuthError, CatDetectClient, CatDetectError
from .const import CONF_TOKEN, CONF_URL, DOMAIN, PLATFORMS
from .coordinator import CatDetectCoordinator

type CatDetectConfigEntry = ConfigEntry[CatDetectCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: CatDetectConfigEntry) -> bool:
    client = CatDetectClient(async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN])
    try:
        await client.get_state()
    except CatDetectAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except CatDetectError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = CatDetectCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    _remove_stale_devices(hass, entry, coordinator.data)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CatDetectConfigEntry) -> bool:
    await entry.runtime_data.stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _remove_stale_devices(hass: HomeAssistant, entry: CatDetectConfigEntry, data: dict) -> None:
    """Удалить из HA камеры и животных, которых больше нет в сервисе."""
    current = {(DOMAIN, f"{entry.entry_id}_camera_{c['id']}") for c in data.get("cameras", [])}
    current |= {(DOMAIN, f"{entry.entry_id}_identity_{i['id']}") for i in data.get("identities", [])}
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers & current:
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)
