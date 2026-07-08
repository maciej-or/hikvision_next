"""hikvision component."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import os
import traceback

from aiohasupervisor.models import LogLevel
from homeassistant.util import slugify
from homeassistant.components.binary_sensor import (
    ENTITY_ID_FORMAT as BINARY_SENSOR_ENTITY_ID_FORMAT,
)
from homeassistant.components.image import ENTITY_ID_FORMAT as IMAGE_ENTITY_ID_FORMAT
from homeassistant.components.sensor import ENTITY_ID_FORMAT as SENSOR_ENTITY_ID_FORMAT
from homeassistant.components.switch import ENTITY_ID_FORMAT as SWITCH_ENTITY_ID_FORMAT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import slugify

from .const import (
    CONF_CONNECTION_SDK,
    DOMAIN,
    resolve_connection_type,
)
from .hikvision_device import HikvisionDevice
from .isapi import ISAPIUnauthorizedError
from .notifications import EventNotificationsView
from .services import setup_services
from .sdk.utils import (
    loadSDK,
    SDKConfig,
    setupSDK,
    shutdownSDK, SDKLogLevel
)

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

    # Create the shared notification_ctx *before* init_coordinators(),
    # because EventSubscription may need to bind its handler to it immediately.
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    if resolve_connection_type(entry.data) == CONF_CONNECTION_SDK:
        if "sdk" not in hass.data[DOMAIN]:
            try:
                sdk = loadSDK()
                sdk_config: SDKConfig = {
                    "log_level": SDKLogLevel.DEBUG,
                    "log_dir": hass.config.config_dir,
                }
                setupSDK(sdk, sdk_config)
                hass.data[DOMAIN]["sdk"] = sdk
                hass.data[DOMAIN]["sdk_ref"] = []
            except Exception as ex:
                msg = f"Cannot initialize {DOMAIN} {device.host} SDK. Error: {ex}\n"
                _LOGGER.error(msg + traceback.format_exc())
                raise ConfigEntryNotReady(msg) from ex

        hass.data[DOMAIN]["sdk_ref"].append(device)
        device.sdk_subscription = hass.data[DOMAIN]["sdk"]

    if "notification_ctx" not in hass.data[DOMAIN]:
        ctx = EventNotificationsView(hass)
        hass.data[DOMAIN]["notification_ctx"] = ctx
        hass.http.register_view(ctx)

    # Assign the shared context to this device early
    device.notification_ctx = hass.data[DOMAIN]["notification_ctx"]

    await device.init_coordinators()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    device.pending_initialization = False

    refresh_disabled_entities_in_registry(hass, device)

    return True


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

    # Stop any long-lived event subscriptions (e.g. ANPR via subscribeEvent)
    if device.event_subscription:
        with suppress(Exception):
            await device.event_subscription.stop()

    if device.sdk_subscription is not None:
        device._cancel_subscribe_reconnect()
        with suppress(Exception):
            await device._stop_video_intercom_remote_config()

        if device.sdk_handle is not None:
            device.sdk_subscription.NET_DVR_CloseAlarmChan_V30(device.sdk_handle)
            _LOGGER.info("SDK Alarm Channel shutdown")
            device.sdk_handle = None
        if device.sdk_user is not None:
            device.sdk_subscription.NET_DVR_Logout_V30(device.sdk_user)
            device.sdk_user = None
            _LOGGER.info("SDK User logout")

        sdk_ref = hass.data.get(DOMAIN, {}).get("sdk_ref")
        if sdk_ref is not None and device in sdk_ref:
            sdk_ref.remove(device)
            # if len(sdk_ref) == 0:
            #     shutdownSDK(device.sdk_subscription)
            #     hass.data[DOMAIN].pop("sdk", None)
            #     hass.data[DOMAIN].pop("sdk_ref", None)
            #     hass.data[DOMAIN].pop("sdk_callback", None)
            #     hass.data[DOMAIN].pop("sdk_callback_registered", None)
        device.sdk_subscription = None

    # Close the separate ext session used for session-login auth if it exists
    if getattr(device, "_ext_session", None):
        with suppress(Exception):
            await device._ext_session.aclose()

    face_snap_images = hass.data.get(DOMAIN, {}).get("face_snap_images")
    if face_snap_images:
        serial = device.device_info.serial_no.lower()
        for camera in device.cameras:
            face_snap_images.pop(slugify(f"{serial}_{camera.id}_face_snap"), None)

    from .const import FACE_VERIFY_IMAGE_SUFFIX

    face_verify_images = hass.data.get(DOMAIN, {}).get("face_verify_images")
    if face_verify_images:
        serial = device.device_info.serial_no.lower()
        face_verify_images.pop(slugify(f"{serial}_{FACE_VERIFY_IMAGE_SUFFIX}"), None)

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

    # 3 -> 4: Replace use_http_notify with connection_type
    if config_entry.version == 3:
        data = dict(config_entry.data)
        if CONF_CONNECTION_TYPE not in data:
            if data.get(CONF_USE_HTTP_NOTIFY, True):
                data[CONF_CONNECTION_TYPE] = CONF_CONNECTION_HTTP_NOTIFY
            else:
                data[CONF_CONNECTION_TYPE] = CONF_CONNECTION_HTTP_CALLBACK
        data.pop(CONF_USE_HTTP_NOTIFY, None)
        hass.config_entries.async_update_entry(config_entry, data=data, version=4)

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
    def update_anpr_plate_entity(event):
        if event.id != "anpr" or not event.anpr_plate_unique_id:
            return
        entity_id = SENSOR_ENTITY_ID_FORMAT.format(event.anpr_plate_unique_id)
        entity = entity_registry.async_get(entity_id)
        if not entity:
            return
        if entity.disabled != event.disabled:
            disabled_by = er.RegistryEntryDisabler.INTEGRATION if event.disabled else None
            entity_registry.async_update_entity(entity_id, disabled_by=disabled_by)

    def update_anpr_image_entity(event):
        if event.id != "anpr" or not event.anpr_image_unique_id:
            return
        entity_id = IMAGE_ENTITY_ID_FORMAT.format(event.anpr_image_unique_id)
        entity = entity_registry.async_get(entity_id)
        if not entity:
            return
        if entity.disabled != event.disabled:
            disabled_by = er.RegistryEntryDisabler.INTEGRATION if event.disabled else None
            entity_registry.async_update_entity(entity_id, disabled_by=disabled_by)

    for camera in device.cameras:
        for event in camera.events_info:
            update_entity(event, SWITCH_ENTITY_ID_FORMAT)
            update_entity(event, BINARY_SENSOR_ENTITY_ID_FORMAT)
            update_anpr_plate_entity(event)
            update_anpr_image_entity(event)

    for event in device.events_info:
        update_entity(event, SWITCH_ENTITY_ID_FORMAT)
        update_entity(event, BINARY_SENSOR_ENTITY_ID_FORMAT)
        update_anpr_plate_entity(event)
        update_anpr_image_entity(event)
