"""Platform for binary sensor integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import ENTITY_ID_FORMAT, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_ISAPI, DOMAIN, EVENTS
from .isapi import ISAPI, AnalogCamera, EventInfo, IPCamera


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add binary sensors for hikvision events states."""

    config = hass.data[DOMAIN][entry.entry_id]
    isapi = config[DATA_ISAPI]

    entities = []

    # Camera Events
    for camera in isapi.cameras:
        for event in camera.supported_events:
            entities.append(CameraEventBinarySensor(isapi, camera, event))

    # NVR Events
    if isapi.device_info.is_nvr:
        for io_event in isapi.device_info.supported_events:
            entities.append(NVREventBinarySensor(isapi, io_event))

    async_add_entities(entities)


class CameraEventBinarySensor(BinarySensorEntity):
    """Event detection sensor."""

    _attr_has_entity_name = True
    _attr_is_on = False

    def __init__(self, isapi, camera: AnalogCamera | IPCamera, event: EventInfo) -> None:
        self.entity_id = ENTITY_ID_FORMAT.format(event.unique_id)
        self._attr_unique_id = self.entity_id
        self._attr_name = EVENTS[event.id]["label"]
        self._attr_device_class = EVENTS[event.id]["device_class"]
        self._attr_device_info = isapi.get_device_info(camera.id)


class NVREventBinarySensor(BinarySensorEntity):
    """IO Event detection sensor."""

    _attr_has_entity_name = True
    _attr_is_on = False

    def __init__(self, isapi: ISAPI, event: EventInfo) -> None:
        self.entity_id = ENTITY_ID_FORMAT.format(event.unique_id)
        self._attr_unique_id = self.entity_id
        self._attr_name = f"{EVENTS[event.id]['label']} {event.channel_id}"
        self._attr_device_class = EVENTS[event.id]["device_class"]
        self._attr_device_info = isapi.get_device_info(0)
