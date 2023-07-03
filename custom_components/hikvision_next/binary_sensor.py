"""Platform for binary sensor integration."""

import asyncio
import logging

from homeassistant.components.binary_sensor import (ENTITY_ID_FORMAT,
                                                    BinarySensorEntity)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback


from .const import DATA_ISAPI, DOMAIN, EVENTS, HIKVISION_EVENT
from .isapi import AlertInfo, AnalogCamera, EventInfo, IPCamera, ISAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add binary sensors for hikvision events states."""

    config = hass.data[DOMAIN][entry.entry_id]
    isapi = config[DATA_ISAPI]

    entities = []
    for camera in isapi.cameras:
        for event in camera.supported_events:
            entities.append(EventBinarySensor(hass, isapi, camera, event))

    async_add_entities(entities)


class EventBinarySensor(BinarySensorEntity):
    """Event detection sensor."""

    _attr_has_entity_name = True
    _attr_is_on = False

    def __init__(self, hass, isapi: ISAPI, camera: AnalogCamera | IPCamera, event: EventInfo) -> None:
        self.entity_id = ENTITY_ID_FORMAT.format(event.unique_id)
        self._attr_unique_id = self.entity_id
        self._attr_name = EVENTS[event.id]["label"]
        self._attr_device_class = EVENTS[event.id]["device_class"]
        self._attr_device_info = isapi.get_device_info(camera.id)

        self._hass = hass
        self._isapi = isapi
        self._camera = camera
        self._event = event

    async def async_added_to_hass(self):
        """Subscribe for event triggers"""
        async def async_triggered(alert: AlertInfo):
            """Update sensor state."""
            if alert.channel_id == self._camera.id and alert.event_id == self._event.id:
                _LOGGER.debug("Event %s triggered on channel %s", alert.event_id, alert.channel_id)
                self._attr_is_on = True
                self.async_write_ha_state()

                # Wait 5 seconds and reset state
                await asyncio.sleep(5)
                self._attr_is_on = False
                self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self._hass, f"{HIKVISION_EVENT}-{self._isapi.device_info.serial_no}", async_triggered
            )
        )
