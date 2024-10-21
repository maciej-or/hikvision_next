"Integration actions."

from httpx import HTTPStatusError

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from .const import ACTION_REBOOT, ATTR_CONFIG_ENTRY_ID, DATA_ISAPI, DOMAIN


def setup_services(hass: HomeAssistant) -> None:
    """Set up the services for the Hikvision component."""

    async def handle_reboot(call: ServiceCall):
        """Handle the service action call."""
        entries = hass.data[DOMAIN]
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        isapi = entries[entry_id][DATA_ISAPI]
        try:
            await isapi.reboot()
        except HTTPStatusError as ex:
            raise HomeAssistantError(ex.response.content) from ex

    hass.services.async_register(DOMAIN, ACTION_REBOOT, handle_reboot)
