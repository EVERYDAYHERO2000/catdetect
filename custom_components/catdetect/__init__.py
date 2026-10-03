"""Интеграция CatDetect: животные на камерах Dahua."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CatDetectAuthError, CatDetectClient, CatDetectError
from .const import CONF_TOKEN, CONF_URL, PLATFORMS
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
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CatDetectConfigEntry) -> bool:
    await entry.runtime_data.stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
