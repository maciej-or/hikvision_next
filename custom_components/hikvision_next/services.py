"Integration actions."

from datetime import datetime
from httpx import HTTPStatusError
import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError

from .const import (
    ACTION_ISAPI_REQUEST,
    ACTION_REBOOT,
    ACTION_SET_OVERLAY,
    ACTION_STOP_OVERLAY,
    ACTION_ENABLE_OVERLAY,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_CAMERA_CHANNEL,
    ATTR_TEXT,
    ATTR_MODE,
    ATTR_DATETIME_FORMAT,
    ATTR_POSITION_X,
    ATTR_POSITION_Y,
    ATTR_INTERVAL_SECONDS,
    ATTR_ENABLED,
    MODE_FIXED,
    MODE_DATETIME,
    DOMAIN,
)
from .isapi import ISAPIForbiddenError, ISAPIUnauthorizedError

ACTION_ISAPI_REQUEST_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): str,
        vol.Required("method"): str,
        vol.Required("path"): str,
        vol.Optional("payload"): str,
    }
)


def validate_datetime_format(value: str) -> str:
    """Validate strftime format string."""
    try:
        datetime.now().strftime(value)
        return value
    except (ValueError, TypeError) as ex:
        raise vol.Invalid(f"Invalid datetime format: {value}") from ex


OVERLAY_SET_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): str,
        vol.Required(ATTR_CAMERA_CHANNEL): vol.All(int, vol.Range(min=1, max=32)),
        vol.Required(ATTR_TEXT): vol.All(str, vol.Length(min=1, max=44)),
        vol.Optional(ATTR_MODE, default=MODE_FIXED): vol.In([MODE_FIXED, MODE_DATETIME]),
        vol.Optional(ATTR_DATETIME_FORMAT): vol.All(str, validate_datetime_format),
        vol.Optional(ATTR_POSITION_X, default=16): vol.All(int, vol.Range(min=0, max=1920)),
        vol.Optional(ATTR_POSITION_Y, default=570): vol.All(int, vol.Range(min=0, max=1080)),
        vol.Optional(ATTR_INTERVAL_SECONDS, default=900): vol.All(int, vol.Range(min=1)),
        vol.Optional(ATTR_ENABLED, default=True): bool,
    }
)

OVERLAY_CONTROL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): str,
        vol.Required(ATTR_CAMERA_CHANNEL): vol.All(int, vol.Range(min=1, max=32)),
    }
)


def setup_services(hass: HomeAssistant) -> None:
    """Set up the services for the Hikvision component."""

    async def handle_reboot(call: ServiceCall):
        """Handle the reboot action call."""
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        entry = hass.config_entries.async_get_entry(entry_id)
        device = entry.runtime_data
        try:
            await device.reboot()
        except (HTTPStatusError, ISAPIForbiddenError, ISAPIUnauthorizedError) as ex:
            raise HomeAssistantError(ex.response.content) from ex

    async def handle_isapi_request(call: ServiceCall) -> ServiceResponse:
        """Handle the custom ISAPI request action call."""
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        entry = hass.config_entries.async_get_entry(entry_id)
        device = entry.runtime_data
        method = call.data.get("method", "POST")
        path = call.data["path"].strip("/")
        payload = call.data.get("payload")
        try:
            response = await device.request(method, path, present="xml", data=payload)
        except (HTTPStatusError, ISAPIForbiddenError, ISAPIUnauthorizedError) as ex:
            if isinstance(ex.response.content, bytes):
                response = ex.response.content.decode("utf-8")
            else:
                response = ex.response.content
        return {"data": response.replace("\r", "")}

    hass.services.async_register(
        DOMAIN,
        ACTION_REBOOT,
        handle_reboot,
    )
    async def handle_set_overlay(call: ServiceCall):
        """Handle the set_overlay action call."""
        from .overlay_manager import async_start_overlay_updates

        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        entry = hass.config_entries.async_get_entry(entry_id)
        device = entry.runtime_data

        config = {
            "camera_channel": call.data[ATTR_CAMERA_CHANNEL],
            "text": call.data[ATTR_TEXT],
            "mode": call.data.get(ATTR_MODE, MODE_FIXED),
            "datetime_format": call.data.get(ATTR_DATETIME_FORMAT),
            "position_x": call.data.get(ATTR_POSITION_X, 16),
            "position_y": call.data.get(ATTR_POSITION_Y, 570),
            "interval_seconds": call.data.get(ATTR_INTERVAL_SECONDS, 900),
            "enabled": call.data.get(ATTR_ENABLED, True),
        }

        try:
            await async_start_overlay_updates(hass, entry_id, device, config)
        except (HTTPStatusError, ISAPIForbiddenError, ISAPIUnauthorizedError) as ex:
            raise HomeAssistantError(ex.response.content) from ex

    async def handle_stop_overlay(call: ServiceCall):
        """Handle the stop_overlay action call."""
        from .overlay_manager import async_stop_overlay_updates

        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        camera_channel = call.data[ATTR_CAMERA_CHANNEL]

        async_stop_overlay_updates(entry_id, camera_channel)

    async def handle_enable_overlay(call: ServiceCall):
        """Handle the enable_overlay action call."""
        from .overlay_manager import async_start_overlay_updates

        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        entry = hass.config_entries.async_get_entry(entry_id)
        device = entry.runtime_data
        camera_channel = call.data[ATTR_CAMERA_CHANNEL]

        # Re-enable using stored config (simplified version)
        # In full implementation, this would retrieve stored config
        config = {
            "camera_channel": camera_channel,
            "text": "Camera",  # Placeholder
            "mode": MODE_FIXED,
            "enabled": True,
        }

        try:
            await async_start_overlay_updates(hass, entry_id, device, config)
        except (HTTPStatusError, ISAPIForbiddenError, ISAPIUnauthorizedError) as ex:
            raise HomeAssistantError(ex.response.content) from ex

    hass.services.async_register(
        DOMAIN,
        ACTION_ISAPI_REQUEST,
        handle_isapi_request,
        schema=ACTION_ISAPI_REQUEST_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        ACTION_SET_OVERLAY,
        handle_set_overlay,
        schema=OVERLAY_SET_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        ACTION_STOP_OVERLAY,
        handle_stop_overlay,
        schema=OVERLAY_CONTROL_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        ACTION_ENABLE_OVERLAY,
        handle_enable_overlay,
        schema=OVERLAY_CONTROL_SCHEMA,
    )
