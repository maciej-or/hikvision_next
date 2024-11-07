"""Config flow for hikvision_next integration."""

from __future__ import annotations

from http import HTTPStatus
import logging
from typing import Any

from httpx import ConnectTimeout, HTTPStatusError
import voluptuous as vol

from homeassistant.components.network import async_get_source_ip
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_ALARM_SERVER_HOST, CONF_SET_ALARM_SERVER, DOMAIN
from .hikvision_device import HikvisionDevice

_LOGGER = logging.getLogger(__name__)


class HikvisionFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for hikvision device."""

    VERSION = 2

    async def get_schema(self, user_input: dict[str, Any]):
        """Get schema with suggested values."""
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default="http://"): str,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_VERIFY_SSL, default=True): bool,
                vol.Required(CONF_SET_ALARM_SERVER, default=True): bool,
                vol.Required(CONF_ALARM_SERVER_HOST): str,
            }
        )
        if self.source == SOURCE_RECONFIGURE:
            reconfigure_entry = self._get_reconfigure_entry()
            return self.add_suggested_values_to_schema(
                schema,
                {**reconfigure_entry.data, **(user_input or {})},
            )
        local_ip = await async_get_source_ip(self.hass)
        return self.add_suggested_values_to_schema(
            schema,
            {CONF_ALARM_SERVER_HOST: f"http://{local_ip}:8123", **(user_input or {})}
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
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

            if not errors:
                if self.source == SOURCE_RECONFIGURE:
                    await self.async_set_unique_id(device.device_info.serial_no, raise_on_progress=False)
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(),
                        data_updates=user_input_validated,
                    )

                # add new device
                await self.async_set_unique_id(device.device_info.serial_no)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=device.device_info.name, data=user_input_validated)

        # show form
        schema = await self.get_schema(user_input)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        """Handle device re-configuration."""
        return await self.async_step_user()
