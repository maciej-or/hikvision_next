"""Config flow for hikvision_next integration."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
import logging
from typing import Any

from httpx import ConnectTimeout, HTTPStatusError
import voluptuous as vol

from homeassistant.components.network import async_get_source_ip
from homeassistant.config_entries import ConfigEntry, ConfigFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.httpx_client import get_async_client

from .const import DATA_ALARM_SERVER_HOST, DATA_SET_ALARM_SERVER, DOMAIN
from .isapi import ISAPI

_LOGGER = logging.getLogger(__name__)


class HikvisionFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for hikvision device."""

    VERSION = 2
    _reauth_entry: ConfigEntry | None = None

    async def get_schema(self, user_input: dict[str, Any]):
        """Get schema with default values or entered by user."""

        local_ip = await async_get_source_ip(self.hass)
        return vol.Schema(
            {
                vol.Required(CONF_HOST, default=user_input.get(CONF_HOST, "http://")): str,
                vol.Required(CONF_USERNAME, default=user_input.get(CONF_USERNAME, "")): str,
                vol.Required(CONF_PASSWORD, default=user_input.get(CONF_PASSWORD, "")): str,
                vol.Required(
                    DATA_SET_ALARM_SERVER,
                    default=user_input.get(DATA_SET_ALARM_SERVER, True),
                ): bool,
                vol.Required(
                    DATA_ALARM_SERVER_HOST,
                    default=user_input.get(DATA_ALARM_SERVER_HOST, f"http://{local_ip}:8123"),
                ): str,
            }
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle a flow initiated by the user."""

        errors = {}

        if user_input is not None:
            try:
                host = user_input[CONF_HOST].rstrip("/")
                username = user_input[CONF_USERNAME]
                password = user_input[CONF_PASSWORD]
                user_input_validated = {
                    **user_input,
                    CONF_HOST: host,
                }

                session = get_async_client(self.hass)
                isapi = ISAPI(host, username, password, session)
                await isapi.get_device_info()

                if self._reauth_entry:
                    self.hass.config_entries.async_update_entry(self._reauth_entry, data=user_input_validated)
                    self.hass.async_create_task(self.hass.config_entries.async_reload(self._reauth_entry.entry_id))
                    return self.async_abort(reason="reauth_successful")

                await self.async_set_unique_id(isapi.device_info.serial_no)
                self._abort_if_unique_id_configured()

            except HTTPStatusError as error:
                status_code = error.response.status_code
                if status_code == HTTPStatus.UNAUTHORIZED:
                    errors["base"] = "invalid_auth"
                elif status_code == HTTPStatus.FORBIDDEN:
                    errors["base"] = "insufficient_permission"
                _LOGGER.error("ISAPI error %s", error)
            except ConnectTimeout:
                errors["base"] = "cannot_connect"
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.error("Unexpected %s %s", {type(ex).__name__}, ex)
                errors["base"] = f"Unexpected {type(ex).__name__}: {ex}"
            else:
                return self.async_create_entry(title=isapi.device_info.name, data=user_input_validated)

        schema = await self.get_schema(user_input or {})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Schedule reauth."""
        _LOGGER.warning("Attempt to reauth in 120s")
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        await asyncio.sleep(120)
        return await self.async_step_user(entry_data)
