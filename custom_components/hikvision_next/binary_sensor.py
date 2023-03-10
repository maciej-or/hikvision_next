"""Platform for binary sensor integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import ENTITY_ID_FORMAT, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_ISAPI, DOMAIN, EVENTS
from .isapi import EventInfo


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add binary sensors for hikvision events states."""

    config = hass.data[DOMAIN][entry.entry_id]
    isapi = config[DATA_ISAPI]

    entities = []
    for event in isapi.events_info:
        entities.append(EventBinarySensor(event))

    async_add_entities(entities)


class EventBinarySensor(BinarySensorEntity):
    """Event detection sensor."""

    _attr_has_entity_name = True
    _attr_is_on = False

    def __init__(self, event: EventInfo) -> None:
        self.entity_id = ENTITY_ID_FORMAT.format(event.unique_id)
        self._attr_unique_id = self.entity_id
        self._attr_name = EVENTS[event.id]["label"]
        self._attr_device_class = EVENTS[event.id]["device_class"]
        self._attr_device_info = event.device_info
