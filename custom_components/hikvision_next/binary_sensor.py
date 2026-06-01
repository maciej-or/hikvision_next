"""Platform for binary sensor integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    ENTITY_ID_FORMAT,
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HikvisionConfigEntry
from .const import EVENTS
from .coordinator import SubscribeStatusCoordinator
from .hikvision_device import HikvisionDevice
from .isapi import EventInfo
from .isapi.const import EVENT_IO


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add binary sensors for hikvision events states."""

    device = entry.runtime_data

    entities = []

    # Video Events
    for camera in device.cameras:
        for event in camera.events_info:
            entities.append(EventBinarySensor(device, camera.id, event))

    # General Events
    for event in device.events_info:
        if "device_class" in EVENTS[event.id]:
            entities.append(EventBinarySensor(device, 0, event))

    async_add_entities(entities)

    # Subscribe / long-lived event connection status (方案3: proper entity + coordinator)
    if device.subscribe_coordinator:
        async_add_entities([HikvisionSubscribeSensor(device.subscribe_coordinator)])


class EventBinarySensor(BinarySensorEntity):
    """Event detection sensor."""

    _attr_has_entity_name = True
    _attr_is_on = False

    def __init__(self, device: HikvisionDevice, device_id: int, event: EventInfo) -> None:
        """Initialize."""
        self.entity_id = ENTITY_ID_FORMAT.format(event.unique_id)
        self._attr_unique_id = self.entity_id
        self._attr_translation_key = event.id
        if event.id == EVENT_IO:
            self._attr_translation_placeholders = {"io_port_id": event.io_port_id}
        self._attr_device_class = EVENTS[event.id]["device_class"]
        self._attr_device_info = device.hass_device_info(device_id)
        self._attr_entity_registry_enabled_default = not event.disabled


class HikvisionSubscribeSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor representing the health of the long-lived subscribeEvent connection."""

    _attr_has_entity_name = True
    _attr_translation_key = "subscribe"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: SubscribeStatusCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device.device_info.serial_no.lower()}_subscribe"
        self.entity_id = ENTITY_ID_FORMAT.format(self._attr_unique_id)
        self._attr_device_info = coordinator.device.hass_device_info(0)

    @property
    def is_on(self) -> bool:
        return self.coordinator.connected if self.coordinator else False

    @property
    def extra_state_attributes(self):
        if self.coordinator and self.coordinator.reason:
            return {"reason": self.coordinator.reason}
        return None
