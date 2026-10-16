"Integration actions."

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
    ACTION_INTERCOM_ANSWER,
    ACTION_INTERCOM_HANGUP,
    ACTION_INTERCOM_REJECT,
    ACTION_ISAPI_REQUEST,
    ACTION_PTZ_MOVE,
    ACTION_PTZ_STOP,
    ACTION_REBOOT,
    ATTR_CONFIG_ENTRY_ID,
    DOMAIN,
)
from .isapi import ISAPIForbiddenError, ISAPIUnauthorizedError
from .sdk.utils import SDKError

ACTION_ISAPI_REQUEST_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): str,
        vol.Required("method"): str,
        vol.Required("path"): str,
        vol.Optional("payload"): str,
    }
)

ACTION_PTZ_MOVE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): str,
        vol.Required("channel_id"): vol.Coerce(int),
        vol.Optional("pan", default=0): vol.Coerce(int),
        vol.Optional("tilt", default=0): vol.Coerce(int),
        vol.Optional("zoom", default=0): vol.Coerce(int),
    }
)

ACTION_PTZ_STOP_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): str,
        vol.Required("channel_id"): vol.Coerce(int),
    }
)

ACTION_INTERCOM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): str,
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
    hass.services.async_register(
        DOMAIN,
        ACTION_ISAPI_REQUEST,
        handle_isapi_request,
        schema=ACTION_ISAPI_REQUEST_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    async def handle_ptz_move(call: ServiceCall) -> None:
        entry = hass.config_entries.async_get_entry(call.data[ATTR_CONFIG_ENTRY_ID])
        device = entry.runtime_data
        camera = device.get_camera_by_id(call.data["channel_id"])
        if camera is None or not camera.support_ptz:
            raise HomeAssistantError(f"Channel {call.data['channel_id']} does not support PTZ")
        await device.ptz_start(
            camera,
            pan=call.data.get("pan", 0),
            tilt=call.data.get("tilt", 0),
            zoom=call.data.get("zoom", 0),
        )

    async def handle_ptz_stop(call: ServiceCall) -> None:
        entry = hass.config_entries.async_get_entry(call.data[ATTR_CONFIG_ENTRY_ID])
        device = entry.runtime_data
        camera = device.get_camera_by_id(call.data["channel_id"])
        if camera is None or not camera.support_ptz:
            raise HomeAssistantError(f"Channel {call.data['channel_id']} does not support PTZ")
        await device.ptz_stop(camera)

    hass.services.async_register(DOMAIN, ACTION_PTZ_MOVE, handle_ptz_move, schema=ACTION_PTZ_MOVE_SCHEMA)
    hass.services.async_register(DOMAIN, ACTION_PTZ_STOP, handle_ptz_stop, schema=ACTION_PTZ_STOP_SCHEMA)

    async def handle_intercom_answer(call: ServiceCall) -> None:
        await _handle_intercom(call, ACTION_INTERCOM_ANSWER)

    async def handle_intercom_reject(call: ServiceCall) -> None:
        await _handle_intercom(call, ACTION_INTERCOM_REJECT)

    async def handle_intercom_hangup(call: ServiceCall) -> None:
        await _handle_intercom(call, ACTION_INTERCOM_HANGUP)

    async def _handle_intercom(call: ServiceCall, action: str) -> None:
        entry = hass.config_entries.async_get_entry(call.data[ATTR_CONFIG_ENTRY_ID])
        device = entry.runtime_data
        if not device.capabilities.support_video_intercom:
            raise HomeAssistantError("Device does not support video intercom")
        try:
            if action == ACTION_INTERCOM_ANSWER:
                await device.intercom_answer()
            elif action == ACTION_INTERCOM_REJECT:
                await device.intercom_reject()
            else:
                await device.intercom_hangup()
        except (ValueError, SDKError) as ex:
            raise HomeAssistantError(str(ex)) from ex

    hass.services.async_register(
        DOMAIN,
        ACTION_INTERCOM_ANSWER,
        handle_intercom_answer,
        schema=ACTION_INTERCOM_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        ACTION_INTERCOM_REJECT,
        handle_intercom_reject,
        schema=ACTION_INTERCOM_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        ACTION_INTERCOM_HANGUP,
        handle_intercom_hangup,
        schema=ACTION_INTERCOM_SCHEMA,
    )
