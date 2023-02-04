"""Config flow for hikvision_next integration."""

from __future__ import annotations

from http import HTTPStatus
import logging
from typing import Any

from httpx import ConnectTimeout, HTTPStatusError
import voluptuous as vol

from homeassistant.components.network import async_get_source_ip
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import device_registry as dr

from .const import DATA_ALARM_SERVER_HOST, DATA_SET_ALARM_SERVER, DOMAIN
from .isapi import ISAPI

_LOGGER = logging.getLogger(__name__)


class HikvisionFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for hikvision device."""

    VERSION = 1

    async def get_schema(self, user_input: dict[str, Any]):
        """Get schema with default values or entered by user"""
        local_ip = await async_get_source_ip(self.hass)
        return vol.Schema(
            {
                vol.Required(
                    CONF_HOST, default=user_input.get(CONF_HOST, "http://")
                ): str,
                vol.Required(
                    CONF_USERNAME, default=user_input.get(CONF_USERNAME, "")
                ): str,
                vol.Required(
                    CONF_PASSWORD, default=user_input.get(CONF_PASSWORD, "")
                ): str,
                vol.Required(
                    DATA_SET_ALARM_SERVER,
                    default=user_input.get(DATA_SET_ALARM_SERVER, True),
                ): bool,
                vol.Required(
                    DATA_ALARM_SERVER_HOST,
                    default=user_input.get(
                        DATA_ALARM_SERVER_HOST, f"http://{local_ip}:8123"
                    ),
                ): str,
            }
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a flow initiated by the user."""

        errors = {}

        if user_input is not None:

            try:
                host = user_input[CONF_HOST]
                username = user_input[CONF_USERNAME]
                password = user_input[CONF_PASSWORD]

                isapi = ISAPI(host, username, password)
                await isapi.get_hardware_info()

                registry = dr.async_get(self.hass)
                if registry.async_get_device(identifiers={(DOMAIN, isapi.serial_no)}):
                    return self.async_abort(reason="already_configured")

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
                _LOGGER.error("Unexpected exception %s", ex)
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=isapi.device_name, data=user_input)

        schema = await self.get_schema(user_input or {})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
