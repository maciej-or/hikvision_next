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
from homeassistant.helpers import selector

from . import HikvisionConfigEntry
from .const import (
    CONF_ALARM_SERVER_HOST,
    CONF_CONNECTION_HTTP_CALLBACK,
    CONF_CONNECTION_HTTP_NOTIFY,
    CONF_CONNECTION_SDK,
    CONF_CONNECTION_TYPE,
    CONF_SET_ALARM_SERVER,
    CONF_USE_HTTP_NOTIFY,
    DOMAIN,
    RTSP_PORT_FORCED,
    resolve_connection_type,
)
from .hikvision_device import HikvisionDevice
from .isapi import ISAPIForbiddenError, ISAPIUnauthorizedError

_LOGGER = logging.getLogger(__name__)

CONNECTION_TYPE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(value=CONF_CONNECTION_HTTP_NOTIFY, label="HTTP long-lived notify"),
            selector.SelectOptionDict(value=CONF_CONNECTION_HTTP_CALLBACK, label="HTTP callback"),
            selector.SelectOptionDict(value=CONF_CONNECTION_SDK, label="SDK"),
        ],
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)


class HikvisionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for hikvision device."""

    VERSION = 4
    _entry: HikvisionConfigEntry
    _user_input: dict[str, Any] | None = None

    def _normalize_user_input(self, user_input: dict[str, Any]) -> dict[str, Any]:
        """Normalize user input before saving config entry data."""
        normalized = {**user_input}
        normalized.pop(CONF_USE_HTTP_NOTIFY, None)

        if normalized[CONF_CONNECTION_TYPE] != CONF_CONNECTION_HTTP_CALLBACK:
            if self.source in (SOURCE_RECONFIGURE, SOURCE_REAUTH):
                normalized[CONF_SET_ALARM_SERVER] = self._entry.data.get(CONF_SET_ALARM_SERVER, False)
                normalized[CONF_ALARM_SERVER_HOST] = self._entry.data.get(CONF_ALARM_SERVER_HOST, "")
            else:
                normalized[CONF_SET_ALARM_SERVER] = False
                normalized[CONF_ALARM_SERVER_HOST] = ""

        return normalized

    def _get_user_suggested(self, user_input: dict[str, Any] | None) -> dict[str, Any]:
        """Get suggested values for the user step."""
        if self.source in (SOURCE_RECONFIGURE, SOURCE_REAUTH):
            return {**self._entry.data, **(user_input or {})}
        return {**(user_input or {})}

    def _get_alarm_server_suggested(self, user_input: dict[str, Any] | None) -> dict[str, Any]:
        """Get suggested values for the alarm server step."""
        suggested: dict[str, Any] = {}
        if self.source in (SOURCE_RECONFIGURE, SOURCE_REAUTH):
            suggested = {**self._entry.data}
        if self._user_input:
            suggested = {**suggested, **self._user_input}
        return {**suggested, **(user_input or {})}

    async def _get_user_schema(self, user_input: dict[str, Any] | None):
        """Build schema for credentials and connection type."""
        suggested = self._get_user_suggested(user_input)
        suggested[CONF_CONNECTION_TYPE] = resolve_connection_type(suggested)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default="http://"): str,
                vol.Optional(CONF_VERIFY_SSL, default=True): bool,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_CONNECTION_TYPE, default=CONF_CONNECTION_HTTP_NOTIFY): CONNECTION_TYPE_SELECTOR,
                vol.Optional(RTSP_PORT_FORCED): vol.And(int, vol.Range(min=1)),
            }
        )
        return self.add_suggested_values_to_schema(schema, suggested)

    async def _get_alarm_server_schema(self, user_input: dict[str, Any] | None):
        """Build schema for HTTP callback alarm server settings."""
        suggested = self._get_alarm_server_suggested(user_input)
        if CONF_ALARM_SERVER_HOST not in suggested:
            local_ip = await async_get_source_ip(self.hass)
            suggested[CONF_ALARM_SERVER_HOST] = f"http://{local_ip}:8123"
        suggested.setdefault(CONF_SET_ALARM_SERVER, False)

        schema = vol.Schema(
            {
                vol.Required(CONF_SET_ALARM_SERVER, default=False): bool,
                vol.Required(CONF_ALARM_SERVER_HOST): str,
            }
        )
        return self.add_suggested_values_to_schema(schema, suggested)

    async def _async_finish_flow(
        self,
        user_input: dict[str, Any],
        errors: dict[str, str],
    ) -> ConfigFlowResult:
        """Validate input and create or update the config entry."""
        try:
            host = user_input[CONF_HOST].rstrip("/")
            user_input_validated = self._normalize_user_input({
                **user_input,
                CONF_HOST: host,
            })

            device = HikvisionDevice(self.hass, data=user_input_validated)
            await device.get_device_info()

        except ISAPIForbiddenError:
            errors["base"] = "insufficient_permission"
        except ISAPIUnauthorizedError:
            errors["base"] = "invalid_auth"
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.error("Unexpected %s %s", {type(ex).__name__}, ex)
            errors["base"] = f"Unexpected {type(ex).__name__}: {ex}"

        if errors:
            if resolve_connection_type(user_input) == CONF_CONNECTION_HTTP_CALLBACK:
                schema = await self._get_alarm_server_schema(user_input)
                return self.async_show_form(step_id="alarm_server", data_schema=schema, errors=errors)

            schema = await self._get_user_schema(user_input)
            return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

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

        await self.async_set_unique_id(device.device_info.serial_no)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=device.device_info.name, data=user_input_validated)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the first step: credentials and connection type."""
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input = {**user_input, CONF_HOST: user_input[CONF_HOST].rstrip("/")}

            if resolve_connection_type(user_input) == CONF_CONNECTION_HTTP_CALLBACK:
                self._user_input = user_input
                return await self.async_step_alarm_server()

            return await self._async_finish_flow(user_input, errors)

        schema = await self._get_user_schema(user_input)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_alarm_server(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the second step: alarm server settings for HTTP callback."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if not self._user_input:
                return await self.async_step_user()

            full_input = {**self._user_input, **user_input}
            return await self._async_finish_flow(full_input, errors)

        schema = await self._get_alarm_server_schema(user_input)
        return self.async_show_form(step_id="alarm_server", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input: Mapping[str, Any] | None = None) -> ConfigFlowResult:
        """Handle device re-configuration."""
        self._entry = self._get_reconfigure_entry()
        return await self.async_step_user()

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Perform reauth upon an authorization error."""
        self._entry = self._get_reauth_entry()
        return await self.async_step_user()