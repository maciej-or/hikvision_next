"""Platform for sensor integration."""

from __future__ import annotations

from typing import Callable

from homeassistant.components.sensor import ENTITY_ID_FORMAT, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HikvisionConfigEntry
from .const import (
    CONF_ALARM_SERVER_HOST,
    CONF_CONNECTION_HTTP_CALLBACK,
    DOMAIN,
    EVENTS,
    FACE_PERSON_PULSE_SECONDS,
    SECONDARY_COORDINATOR,
    resolve_connection_type,
)
from .isapi import StorageInfo, EventInfo
from .hikvision_device import HikvisionDevice

NOTIFICATION_HOST_KEYS = [
    "protocol_type",
    "address", # ip_address or host_name
    "port_no",
    "path",
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add diagnostic sensors for hikvision alarm server settings and storage items."""

    device = entry.runtime_data
    config = entry.data if entry else {}
    coordinator = device.coordinators.get(SECONDARY_COORDINATOR)

    entities = []
    seen_unique_ids: set[str] = set()

    def add_entity(entity: SensorEntity) -> None:
        unique_id = entity.unique_id
        if unique_id in seen_unique_ids:
            return
        seen_unique_ids.add(unique_id)
        entities.append(entity)

    if coordinator:
        if resolve_connection_type(config) == CONF_CONNECTION_HTTP_CALLBACK:
            for key in NOTIFICATION_HOST_KEYS:
                add_entity(AlarmServerSensor(coordinator, key))

        for item in list(device.storage):
            add_entity(StorageSensor(coordinator, item))

    for event in device.events_info:
        if event.id == "anpr":
            add_entity(AnprLicensePlateSensor(device, 0, event))
        elif event.id == "face":
            add_entity(FacePersonSensor(device, 0, event))
        elif "device_class" not in EVENTS[event.id]:
            add_entity(AlarmSensor(device, 0, event))

    for camera in device.cameras:
        for event in camera.events_info:
            if event.id == "anpr":
                add_entity(AnprLicensePlateSensor(device, camera.id, event))
            elif "device_class" not in EVENTS[event.id]:
                add_entity(AlarmSensor(device, camera.id, event))

    if entities:
        async_add_entities(entities, True)


class AlarmServerSensor(CoordinatorEntity, SensorEntity):
    """Alarm Server settings sensor."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:ip-network"

    def __init__(self, coordinator, key: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_unique_id = f"{device.device_info.serial_no}_{CONF_ALARM_SERVER_HOST}_{key}"
        self.entity_id = ENTITY_ID_FORMAT.format(self.unique_id)
        self._attr_device_info = device.hass_device_info()
        self._attr_translation_key = f"notifications_host_{key}"
        self.key = key

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        host = self.coordinator.data.get(CONF_ALARM_SERVER_HOST)
        if host is None:
            return None
        return host.get(self.key)


class AlarmSensor(SensorEntity):
    """Alarm sensor."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device: HikvisionDevice, device_id: int, event: EventInfo) -> None:
        """Initialize."""

        self.entity_id = ENTITY_ID_FORMAT.format(event.unique_id)
        self._attr_unique_id = self.entity_id
        self._attr_translation_key = event.id
        self._attr_name = EVENTS[event.id].get("name", None)
        self._attr_icon = EVENTS[event.id].get("icon", "mdi:human")
        self._attr_device_info = device.hass_device_info(device_id)
        self._attr_entity_registry_enabled_default = not event.disabled

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        return "unavailable"


class FacePersonSensor(SensorEntity):
    """Text sensor exposing the last person recognized at the door."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:human"

    def __init__(self, device: HikvisionDevice, device_id: int, event: EventInfo) -> None:
        """Initialize."""
        self.entity_id = ENTITY_ID_FORMAT.format(event.unique_id)
        self._attr_unique_id = self.entity_id
        self._attr_translation_key = "face"
        self._attr_device_info = device.hass_device_info(device_id)
        self._attr_entity_registry_enabled_default = not event.disabled
        self._attr_native_value = "unknown"
        self._face_reset_unsub: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        """Register for direct state updates from SDK/ISAPI event handlers."""
        await super().async_added_to_hass()
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        sensors = domain_data.setdefault("event_text_sensors", {})
        sensors[self.unique_id] = self

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from direct state updates."""
        self._cancel_face_reset()
        sensors = self.hass.data.get(DOMAIN, {}).get("event_text_sensors", {})
        sensors.pop(self.unique_id, None)
        await super().async_will_remove_from_hass()

    def _cancel_face_reset(self) -> None:
        if self._face_reset_unsub is not None:
            self._face_reset_unsub()
            self._face_reset_unsub = None

    def _schedule_face_reset(self) -> None:
        """Reset to unknown after a short pulse so automations can re-trigger."""
        if self.hass is None:
            return
        self._cancel_face_reset()

        def _reset_to_unknown(_now) -> None:
            self._face_reset_unsub = None
            self._attr_native_value = "unknown"
            self._attr_extra_state_attributes = {}
            if self.platform is not None:
                self.schedule_update_ha_state()

        self._face_reset_unsub = async_call_later(
            self.hass,
            FACE_PERSON_PULSE_SECONDS,
            _reset_to_unknown,
        )

    def set_person(self, person: str, attributes: dict | None = None) -> None:
        """Update the recognized person state."""
        self._attr_native_value = person
        self._attr_extra_state_attributes = attributes or {}
        if self.platform is not None:
            self.schedule_update_ha_state()
        self._schedule_face_reset()


class AnprLicensePlateSensor(SensorEntity):
    """Text sensor exposing the last recognized license plate."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:car"

    def __init__(self, device: HikvisionDevice, device_id: int, event: EventInfo) -> None:
        """Initialize."""
        plate_unique_id = event.anpr_plate_unique_id or f"{event.unique_id}_plate"
        self._attr_unique_id = plate_unique_id
        self.entity_id = ENTITY_ID_FORMAT.format(self._attr_unique_id)
        self._attr_translation_key = "anpr_license_plate"
        self._attr_device_info = device.hass_device_info(device_id)
        self._attr_entity_registry_enabled_default = not event.disabled


class StorageSensor(CoordinatorEntity, SensorEntity):
    """HDD, NAS status sensor."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:harddisk"

    def __init__(self, coordinator, hdd: StorageInfo) -> None:
        """Initialize."""
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_unique_id = f"{device.device_info.serial_no}_{hdd.id}_{hdd.name}"
        self.entity_id = ENTITY_ID_FORMAT.format(self.unique_id)
        self._attr_device_info = device.hass_device_info()
        self._attr_name = f"{hdd.type} {hdd.name}"
        self.hdd = hdd

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        hdd = self.coordinator.device.get_storage_device_by_id(self.hdd.id)
        return str(hdd.status).upper() if hdd else None

    @property
    def extra_state_attributes(self):
        """Return extra attributes."""
        attrs = {}
        attrs["type"] = self.hdd.type
        attrs["capacity"] = self.hdd.capacity
        attrs["freespace"] = self.hdd.freespace
        if self.hdd.ip:
            attrs["ip"] = self.hdd.ip
        return attrs