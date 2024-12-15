"""Config flow for hikvision_next integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.network import async_get_source_ip
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL

from . import HikvisionConfigEntry
from .const import (
    CONF_ALARM_SERVER_HOST,
    CONF_SET_ALARM_SERVER,
    DOMAIN,
    RTSP_PORT_FORCED,
)
from .hikvision_device import HikvisionDevice
from .isapi import ISAPIForbiddenError, ISAPIUnauthorizedError

_LOGGER = logging.getLogger(__name__)


class HikvisionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for hikvision device."""

    VERSION = 3
    _entry: HikvisionConfigEntry

    async def get_schema(self, user_input: dict[str, Any]):
        """Get schema with suggested values."""
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default="http://"): str,
                vol.Optional(CONF_VERIFY_SSL, default=True): bool,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_SET_ALARM_SERVER, default=True): bool,
                vol.Required(CONF_ALARM_SERVER_HOST): str,
                vol.Optional(RTSP_PORT_FORCED): vol.And(int, vol.Range(min=1)),
            }
        )
        if self.source in (SOURCE_RECONFIGURE, SOURCE_REAUTH):
            return self.add_suggested_values_to_schema(
                schema,
                {**self._entry.data, **(user_input or {})},
            )
        local_ip = await async_get_source_ip(self.hass)
        return self.add_suggested_values_to_schema(
            schema,
            {CONF_ALARM_SERVER_HOST: f"http://{local_ip}:8123", **(user_input or {})},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle a flow initiated by the user."""

        errors = {}

        if user_input is not None:
            try:
                host = user_input[CONF_HOST].rstrip("/")
                user_input_validated = {
                    **user_input,
                    CONF_HOST: host,
                }

                device = HikvisionDevice(self.hass, data=user_input_validated)
                await device.get_device_info()

            except ISAPIForbiddenError:
                errors["base"] = "insufficient_permission"
            except ISAPIUnauthorizedError:
                errors["base"] = "invalid_auth"
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.error("Unexpected %s %s", {type(ex).__name__}, ex)
                errors["base"] = f"Unexpected {type(ex).__name__}: {ex}"

            if not errors:
                if self.source == SOURCE_RECONFIGURE:
                    await self.async_set_unique_id(device.device_info.serial_no, raise_on_progress=False)
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        self._entry,
                        data_updates=user_input_validated,
                    )
                if self.source == SOURCE_REAUTH:
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(entry=self._entry, data=user_input_validated)

                # add new device
                await self.async_set_unique_id(device.device_info.serial_no)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=device.device_info.name, data=user_input_validated)

        # show form
        schema = await self.get_schema(user_input)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input: Mapping[str, Any] | None = None) -> ConfigFlowResult:
        """Handle device re-configuration."""
        self._entry = self._get_reconfigure_entry()
        return await self.async_step_user()

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Perform reauth upon an authorization error."""
        self._entry = self._get_reauth_entry()
        return await self.async_step_user()
