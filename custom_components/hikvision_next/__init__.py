"""hikvision component."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import traceback

from homeassistant.components.binary_sensor import (
    ENTITY_ID_FORMAT as BINARY_SENSOR_ENTITY_ID_FORMAT,
)
from homeassistant.components.switch import ENTITY_ID_FORMAT as SWITCH_ENTITY_ID_FORMAT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import slugify

from .const import (
    CONF_ALARM_SERVER_HOST,
    DOMAIN,
    HOLIDAY_MODE,
    NOTIFICATION_HOST_KEYS,
    SECONDARY_COORDINATOR,
)
from .hikvision_device import HikvisionDevice
from .isapi import ISAPIUnauthorizedError
from .notifications import EventNotificationsView
from .services import setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
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

    refresh_disabled_entities_in_registry(hass, device)
    remove_orphaned_entities_from_registry(hass, entry)

    return True


async def async_remove_config_entry_device(hass: HomeAssistant, config_entry, device_entry) -> bool:
    """Delete device if not entities."""
    if not getattr(device_entry, "via_device_id", None) and not getattr(device_entry, "via_device", None):
        _LOGGER.error(
            "You cannot delete the NVR device via the device delete method.  Please remove the integration instead"
        )
        return False
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HikvisionConfigEntry) -> bool:
    """Unload a config entry."""

    # Unload a config entry
    unload_ok = all(
        await asyncio.gather(
            *[hass.config_entries.async_forward_entry_unload(entry, platform) for platform in PLATFORMS]
        )
    )

    # Reset alarm server after it has been set
    device = entry.runtime_data
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


@callback
def remove_orphaned_entities_from_registry(hass: HomeAssistant, entry: HikvisionConfigEntry) -> None:
    """Remove entity registry entries that are no longer provided by the integration.

    Keeps the registry in sync after unique_id changes or removed entities, so
    Home Assistant does not report "This entity is no longer provided by the
    integration".

    Camera entities are excluded from removal to preserve them when streams are
    temporarily unavailable due to connection issues.
    """
    from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
    from homeassistant.components.image import DOMAIN as IMAGE_DOMAIN

    device = entry.runtime_data
    entity_registry = er.async_get(hass)

    serial = device.device_info.serial_no
    serial_slug = slugify(serial.lower())

    expected: set[str] = set()

    for camera in device.cameras:
        # Add camera stream entities - preserve all camera/image entities
        # even if streams are temporarily unavailable due to connection issues
        for stream in camera.streams:
            expected.add(slugify(f"{serial.lower()}_{stream.id}"))
            if stream.type_id == 1:
                expected.add(slugify(f"{serial.lower()}_{stream.id}_snapshot"))

        for event in camera.events_info:
            expected.add(f"switch.{event.unique_id}")
            expected.add(f"binary_sensor.{event.unique_id}")

    for event in device.events_info:
        expected.add(f"switch.{event.unique_id}")
        expected.add(f"binary_sensor.{event.unique_id}")

    for port_no in range(1, device.capabilities.output_ports + 1):
        expected.add(f"switch.{serial_slug}_{port_no}_alarm_output")

    if device.capabilities.support_holiday_mode:
        expected.add(f"{serial_slug}_{HOLIDAY_MODE}")

    if SECONDARY_COORDINATOR in device.coordinators:
        for key in NOTIFICATION_HOST_KEYS:
            expected.add(f"{serial}_{CONF_ALARM_SERVER_HOST}_{key}")
        for item in device.storage:
            expected.add(f"{serial}_{item.id}_{item.name}")

    for reg_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        # Never remove camera or image entities - they may be temporarily unavailable
        # due to stream connectivity issues and will recover when connection is restored
        if reg_entry.domain in (CAMERA_DOMAIN, IMAGE_DOMAIN):
            continue

        if reg_entry.unique_id not in expected:
            _LOGGER.debug(
                "Removing orphaned entity %s (unique_id: %s)",
                reg_entry.entity_id,
                reg_entry.unique_id,
            )
            entity_registry.async_remove(reg_entry.entity_id)
