"""hikvision component."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging

from httpx import TimeoutException

from homeassistant.components.binary_sensor import (
    ENTITY_ID_FORMAT as BINARY_SENSOR_ENTITY_ID_FORMAT,
)
from homeassistant.components.switch import ENTITY_ID_FORMAT as SWITCH_ENTITY_ID_FORMAT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.httpx_client import get_async_client

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
    Platform.IMAGE,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up integration from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    session = get_async_client(hass)
    isapi = ISAPI(host, username, password, session)
    isapi.pending_initialization = True
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

    isapi.pending_initialization = False

    # Only initialise view once if multiple instances of integration
    if get_first_instance_unique_id(hass) == entry.unique_id:
        hass.http.register_view(EventNotificationsView(hass))

    refresh_disabled_entities_in_registry(hass, isapi)

    return True


async def async_remove_config_entry_device(hass: HomeAssistant, config_entry, device_entry) -> bool:
    """Delete device if not entities."""
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
        with suppress(Exception):
            await isapi.set_alarm_server("http://0.0.0.0:80", "/")

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


def get_first_instance_unique_id(hass: HomeAssistant) -> int:
    """Get entry unique_id for first instance of integration."""
    entry = [entry for entry in hass.config_entries.async_entries(DOMAIN) if not entry.disabled_by][0]
    return entry.unique_id


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Migrate old entry."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)

    # 1 -> 2: Config entry unique_id format changed
    if config_entry.version == 1:
        unique_id = config_entry.unique_id
        if isinstance(unique_id, list) and len(unique_id) == 1 and isinstance(unique_id[0], list):
            new_unique_id = unique_id[0][1]
            hass.config_entries.async_update_entry(
                config_entry,
                data={**config_entry.data},
                unique_id=new_unique_id,
            )

        config_entry.version = 2

        _LOGGER.debug(
            "Migration to version %s.%s successful",
            config_entry.version,
            config_entry.minor_version,
        )

    return True


def refresh_disabled_entities_in_registry(hass: HomeAssistant, isapi: ISAPI):
    """Set disable state according to Notify Surveillance Center flag."""

    def update_entity(event, ENTITY_ID_FORMAT):
        entity_id = ENTITY_ID_FORMAT.format(event.unique_id)
        entity = entity_registry.async_get(entity_id)
        if not entity:
            return
        if entity.disabled != event.disabled:
            disabled_by = er.RegistryEntryDisabler.INTEGRATION if event.disabled else None
            entity_registry.async_update_entity(entity_id, disabled_by=disabled_by)

    entity_registry = er.async_get(hass)
    for camera in isapi.cameras:
        for event in camera.events_info:
            update_entity(event, SWITCH_ENTITY_ID_FORMAT)
            update_entity(event, BINARY_SENSOR_ENTITY_ID_FORMAT)

    for event in isapi.device_info.events_info:
        update_entity(event, SWITCH_ENTITY_ID_FORMAT)
        update_entity(event, BINARY_SENSOR_ENTITY_ID_FORMAT)
