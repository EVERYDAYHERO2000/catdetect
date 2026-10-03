"""Настройка интеграции через интерфейс HA."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CatDetectAuthError, CatDetectClient, CatDetectError
from .const import CONF_TOKEN, CONF_URL, DOMAIN


class CatDetectConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, url: str, token: str) -> str | None:
        client = CatDetectClient(async_get_clientsession(self.hass), url, token)
        try:
            await client.get_state()
        except CatDetectAuthError:
            return "invalid_auth"
        except CatDetectError:
            return "cannot_connect"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                url = f"http://{url}"
            await self.async_set_unique_id(url)
            self._abort_if_unique_id_configured()
            if (err := await self._validate(url, user_input[CONF_TOKEN].strip())) is None:
                return self.async_create_entry(
                    title="CatDetect", data={CONF_URL: url, CONF_TOKEN: user_input[CONF_TOKEN].strip()}
                )
            errors["base"] = err
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_URL, default=(user_input or {}).get(CONF_URL, "http://")): str,
                vol.Required(CONF_TOKEN): str,
            }),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            if (err := await self._validate(entry.data[CONF_URL], token)) is None:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: token})
            errors["base"] = err
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}), errors=errors
        )
