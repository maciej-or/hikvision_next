"""hikvision component"""

from __future__ import annotations

import asyncio
import logging

from httpx import TimeoutException

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
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

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up integration from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    isapi = ISAPI(host, username, password)
    try:
        await isapi.get_hardware_info()
        await isapi.get_cameras()
        device_info = isapi.hass_device_info()
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(config_entry_id=entry.entry_id, **device_info)
    except (asyncio.TimeoutError, TimeoutException) as ex:
        raise ConfigEntryNotReady(f"Timeout while connecting to {host}. Cannot initialize {DOMAIN}") from ex
    except Exception as ex:  # pylint: disable=broad-except
        raise ConfigEntryNotReady(
            f"Unknown error connecting to {host}. Cannot initialize {DOMAIN}. Error is {ex}"
        ) from ex

    coordinators = {}

    coordinators[EVENTS_COORDINATOR] = EventsCoordinator(hass, isapi)

    if isapi.device_info.support_holiday_mode or isapi.device_info.support_alarm_server:
        coordinators[SECONDARY_COORDINATOR] = SecondaryCoordinator(hass, isapi)

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_SET_ALARM_SERVER: entry.data[DATA_SET_ALARM_SERVER],
        DATA_ALARM_SERVER_HOST: entry.data[DATA_ALARM_SERVER_HOST],
        DATA_ISAPI: isapi,
        **coordinators,
    }

    if entry.data[DATA_SET_ALARM_SERVER] and isapi.device_info.support_alarm_server:
        await isapi.set_alarm_server(entry.data[DATA_ALARM_SERVER_HOST], ALARM_SERVER_PATH)

    for coordinator in coordinators.values():
        await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Only initialise view once if multiple instances of integration
    if get_first_instance_unique_id(hass) == entry.unique_id:
        hass.http.register_view(EventNotificationsView(hass))

    return True


async def async_remove_config_entry_device(hass: HomeAssistant, config_entry, device_entry) -> bool:
    """Delete device if not entities"""
    if not device_entry.via_device_id:
        _LOGGER.error(
            "You cannot delete the NVR device via the device delete method.  Please remove the integration instead"
        )
        return False
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    config = hass.data[DOMAIN][entry.entry_id]

    # Unload a config entry
    unload_ok = all(
        await asyncio.gather(
            *[hass.config_entries.async_forward_entry_unload(entry, platform) for platform in PLATFORMS]
        )
    )

    # Reset alarm server after it has been set
    if config[DATA_SET_ALARM_SERVER]:
        isapi = config[DATA_ISAPI]
        try:
            await isapi.set_alarm_server("http://0.0.0.0:80", "/")
        except Exception:  # pylint: disable=broad-except
            pass

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


def get_first_instance_unique_id(hass: HomeAssistant) -> int:
    """Get entry unique_id for first instance of integration"""
    entry = [entry for entry in hass.config_entries.async_entries(DOMAIN) if not entry.disabled_by][0]
    return entry.unique_id
