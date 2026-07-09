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
from homeassistant.util import slugify

from . import HikvisionConfigEntry
from .const import DOMAIN, EVENTS, LOCK_EVENT_IDS, TEXT_SENSOR_EVENT_IDS
from .coordinator import IntercomStatusCoordinator, SubscribeStatusCoordinator
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
    seen_unique_ids: set[str] = set()

    def add_entity(entity: EventBinarySensor) -> None:
        unique_id = entity.unique_id
        if unique_id in seen_unique_ids:
            return
        seen_unique_ids.add(unique_id)
        entities.append(entity)

    # Video Events
    for camera in device.cameras:
        for event in camera.events_info:
            add_entity(EventBinarySensor(device, camera.id, event))

    # General Events
    for event in device.events_info:
        if event.id in TEXT_SENSOR_EVENT_IDS or event.id in LOCK_EVENT_IDS:
            continue
        event_meta = EVENTS[event.id]
        if "device_class" in event_meta or "icon" in event_meta:
            add_entity(EventBinarySensor(device, 0, event))

    async_add_entities(entities)

    connectivity_entities = []
    if device.subscribe_coordinator:
        connectivity_entities.append(HikvisionSubscribeSensor(device.subscribe_coordinator))
    if device.intercom_coordinator:
        connectivity_entities.append(HikvisionIntercomConnectSensor(device.intercom_coordinator))
    if connectivity_entities:
        async_add_entities(connectivity_entities)


class EventBinarySensor(BinarySensorEntity):
    """Event detection sensor."""

    _attr_has_entity_name = True

    def __init__(self, device: HikvisionDevice, device_id: int, event: EventInfo) -> None:
        """Initialize."""
        self.entity_id = ENTITY_ID_FORMAT.format(event.unique_id)
        self._attr_unique_id = self.entity_id
        self._attr_is_on = False
        self._attr_translation_key = event.id
        if event.id == EVENT_IO:
            self._attr_translation_placeholders = {"io_port_id": event.io_port_id}
        if "device_class" in EVENTS[event.id]:
            self._attr_device_class = EVENTS[event.id]["device_class"]
        if "icon" in EVENTS[event.id]:
            self._attr_icon = EVENTS[event.id]["icon"]
        self._attr_device_info = device.hass_device_info(device_id)
        self._attr_entity_registry_enabled_default = not event.disabled

    async def async_added_to_hass(self) -> None:
        """Register for direct state updates from SDK/ISAPI event handlers."""
        await super().async_added_to_hass()
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        sensors = domain_data.setdefault("event_binary_sensors", {})
        sensors[self.unique_id] = self

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from direct state updates."""
        sensors = self.hass.data.get(DOMAIN, {}).get("event_binary_sensors", {})
        sensors.pop(self.unique_id, None)
        await super().async_will_remove_from_hass()

    def set_active(self, active: bool) -> None:
        """Update the binary sensor on/off state."""
        self._attr_is_on = active
        self.schedule_update_ha_state()


class HikvisionSubscribeSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for SDK alarm channel / subscribeEvent connectivity."""

    _attr_has_entity_name = True
    _attr_translation_key = "subscribe"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: SubscribeStatusCoordinator) -> None:
        super().__init__(coordinator)
        serial = slugify(coordinator.device.device_info.serial_no.lower())
        self._attr_unique_id = f"{serial}_subscribe"
        self._attr_device_info = coordinator.device.hass_device_info(0)

    @property
    def is_on(self) -> bool:
        return self.coordinator.connected if self.coordinator else False

    @property
    def extra_state_attributes(self):
        if self.coordinator and self.coordinator.reason:
            return {"reason": self.coordinator.reason}
        return None


class HikvisionIntercomConnectSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for VideoIntercomRemoteConfig session connectivity."""

    _attr_has_entity_name = True
    _attr_translation_key = "intercom_connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: IntercomStatusCoordinator) -> None:
        super().__init__(coordinator)
        serial = slugify(coordinator.device.device_info.serial_no.lower())
        self._attr_unique_id = f"{serial}_intercom_connectivity"
        self._attr_device_info = coordinator.device.hass_device_info(0)

    @property
    def is_on(self) -> bool:
        return self.coordinator.connected if self.coordinator else False

    @property
    def extra_state_attributes(self):
        if self.coordinator and self.coordinator.reason:
            return {"reason": self.coordinator.reason}
        return None
