"""hikvision component"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    ALARM_SERVER_PATH,
    DATA_ALARM_SERVER_HOST,
    DATA_ISAPI,
    DATA_SET_ALARM_SERVER,
    DOMAIN,
    EVENTS_COORDINATOR,
    SECONDARY_COORDINATOR,
)
from .coordinator import EventsCoordinator, SecondaryCoordinator
from .isapi import ISAPI
from .notifications import EventNotificationsView

PLATFORMS = [Platform.SWITCH, Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up integration from a config entry."""

    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    isapi = ISAPI(host, username, password)
    try:
        await isapi.get_hardware_info()

        if isapi.is_nvr:
            nvr_device_info = isapi.device_info
            device_registry = dr.async_get(hass)
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id, **nvr_device_info
            )
            await isapi.get_nvr_capabilities()
        else:
            await isapi.get_ip_camera_capabilities()
    except Exception as ex:  # pylint: disable=broad-except
        if not isapi.handle_exception(ex, f"Cannot initialize {DOMAIN}"):
            raise ex

    coordinators = {}
    coordinators[EVENTS_COORDINATOR] = EventsCoordinator(hass, isapi)
    if isapi.holidays_support or isapi.alarm_server_support:
        coordinators[SECONDARY_COORDINATOR] = SecondaryCoordinator(hass, isapi)

    if entry.data[DATA_SET_ALARM_SERVER] and isapi.alarm_server_support:
        await isapi.set_alarm_server(
            entry.data[DATA_ALARM_SERVER_HOST], ALARM_SERVER_PATH
        )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_SET_ALARM_SERVER: entry.data[DATA_SET_ALARM_SERVER],
        DATA_ALARM_SERVER_HOST: entry.data[DATA_ALARM_SERVER_HOST],
        DATA_ISAPI: isapi,
        **coordinators,
    }

    for coordinator in coordinators.values():
        await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    hass.http.register_view(EventNotificationsView)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):

        # reset alarm server if has been set
        config = hass.data[DOMAIN][entry.entry_id]
        if config[DATA_SET_ALARM_SERVER]:
            isapi = config[DATA_ISAPI]
            try:
                await isapi.set_alarm_server("http://0.0.0.0:80", "/")
            except Exception:  # pylint: disable=broad-except
                pass
        if unload_ok:
            del hass.data[DOMAIN][entry.entry_id]

    return unload_ok
