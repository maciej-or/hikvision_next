"""hikvision component."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import traceback
from homeassistant.util import slugify
from homeassistant.components.alarm_control_panel import ENTITY_ID_FORMAT as ALARM_ENTITY_ID_FORMAT
from homeassistant.components.binary_sensor import (
    ENTITY_ID_FORMAT as BINARY_SENSOR_ENTITY_ID_FORMAT,
)
from homeassistant.components.switch import ENTITY_ID_FORMAT as SWITCH_ENTITY_ID_FORMAT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .hikvision_device import HikvisionDevice
from .isapi import ISAPIUnauthorizedError
from .notifications import EventNotificationsView
from .services import setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.IMAGE,
]

type HikvisionConfigEntry = ConfigEntry[HikvisionDevice]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Hikvision component."""

    setup_services(hass)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: HikvisionConfigEntry) -> bool:
    """Set up integration from a config entry."""
    device = HikvisionDevice(hass, entry)
    device.pending_initialization = True
    try:
        await device.get_hardware_info()
        device_info = device.hass_device_info()
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(config_entry_id=entry.entry_id, **device_info)
    except ISAPIUnauthorizedError as ex:
        raise ConfigEntryAuthFailed from ex
    except Exception as ex:  # pylint: disable=broad-except
        msg = f"Cannot initialize {DOMAIN} {device.host}. Error: {ex}\n"
        _LOGGER.error(msg + traceback.format_exc())
        raise ConfigEntryNotReady(msg) from ex

    entry.runtime_data = device

    await device.init_coordinators()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    device.pending_initialization = False

    # Only initialise view once if multiple instances of integration
    if get_first_instance_unique_id(hass) == entry.unique_id:
        hass.http.register_view(EventNotificationsView(hass))

    if device.device_info.is_security_panel:
        remove_orphaned_partition_entities(hass, entry, device)
    else:
        refresh_disabled_entities_in_registry(hass, device)

    return True


def remove_orphaned_partition_entities(hass: HomeAssistant, entry: HikvisionConfigEntry, device: HikvisionDevice):
    """Remove alarm_control_panel entities for partitions no longer configured/enabled.

    The panel always reports every possible partition slot (e.g. 16), regardless of how many
    are actually in use, and get_partitions() only keeps the enabled ones -- so a partition
    that gets disabled on the panel, or was previously included by mistake, needs its stale
    entity removed here rather than just left "unavailable" forever.
    """
    known_unique_ids = {ALARM_ENTITY_ID_FORMAT.format(partition.unique_id) for partition in device.partitions}
    entity_registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity_entry.domain == "alarm_control_panel" and entity_entry.unique_id not in known_unique_ids:
            entity_registry.async_remove(entity_entry.entity_id)


async def async_remove_config_entry_device(hass: HomeAssistant, config_entry, device_entry) -> bool:
    """Delete device if not entities."""
    if not device_entry.via_device_id:
        _LOGGER.error(
            "You cannot delete the NVR device via the device delete method.  Please remove the integration instead"
        )
        return False
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HikvisionConfigEntry) -> bool:
    """Unload a config entry."""

    device = entry.runtime_data
    if device.arming_listener:
        await device.arming_listener.async_stop()

    # Unload a config entry
    unload_ok = all(
        await asyncio.gather(
            *[hass.config_entries.async_forward_entry_unload(entry, platform) for platform in PLATFORMS]
        )
    )

    # Reset alarm server after it has been set
    if device.control_alarm_server_host:
        with suppress(Exception):
            await device.set_alarm_server("http://0.0.0.0:80", "/")

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

    # 2 -> 3: Delete previous alaram server sensor entities
    if config_entry.version == 2:
        old_keys = ["protocoltype", "ipaddress", "portno", "url"]
        entity_registry = er.async_get(hass)
        for key in old_keys:
            entity_id = f"sensor.{slugify(config_entry.unique_id)}_alarm_server_{key}"
            entity = entity_registry.async_get(entity_id)
            if entity:
                entity_registry.async_remove(entity_id)

        hass.config_entries.async_update_entry(
            config_entry,
            version=3,
        )

    # 3 -> 4: Force-recreate sensor entities so they pick up entity_category=DIAGNOSTIC.
    # Registry entries created before this category was consistently applied don't get
    # it retroactively just because the entity is re-added on a later setup.
    if config_entry.version == 3:
        entity_registry = er.async_get(hass)
        for entry_entity in er.async_entries_for_config_entry(entity_registry, config_entry.entry_id):
            if entry_entity.domain == "sensor":
                entity_registry.async_remove(entry_entity.entity_id)

        hass.config_entries.async_update_entry(
            config_entry,
            version=4,
        )

    _LOGGER.debug(
        "Migration to version %s.%s successful",
        config_entry.version,
        config_entry.minor_version,
    )

    return True


def refresh_disabled_entities_in_registry(hass: HomeAssistant, device: HikvisionDevice):
    """Set disable state according to Notify Surveillance Center flag."""

    def update_entity(event, ENTITY_ID_FORMAT):
        entity_id = ENTITY_ID_FORMAT.format(event.unique_id)
        entity = entity_registry.async_get(entity_id)
        if not entity:
            return
        if entity.disabled != event.disabled:
            _LOGGER.warning(
                "refresh_disabled_entities_in_registry: %s registry.disabled=%s (disabled_by=%s) "
                "-> event.disabled=%s (notifications=%s); writing disabled_by=%s",
                entity_id,
                entity.disabled,
                entity.disabled_by,
                event.disabled,
                event.notifications,
                er.RegistryEntryDisabler.INTEGRATION if event.disabled else None,
            )
            disabled_by = er.RegistryEntryDisabler.INTEGRATION if event.disabled else None
            entity_registry.async_update_entity(entity_id, disabled_by=disabled_by)

    entity_registry = er.async_get(hass)
    for camera in device.cameras:
        for event in camera.events_info:
            update_entity(event, SWITCH_ENTITY_ID_FORMAT)
            update_entity(event, BINARY_SENSOR_ENTITY_ID_FORMAT)

    for event in device.events_info:
        update_entity(event, SWITCH_ENTITY_ID_FORMAT)
        update_entity(event, BINARY_SENSOR_ENTITY_ID_FORMAT)
