"""Platform for light integration."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ENTITY_ID_FORMAT,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from . import HikvisionConfigEntry
from .const import EVENTS_COORDINATOR


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add hikvision_next light entities from a config_entry."""
    device = entry.runtime_data
    coordinator = device.coordinators.get(EVENTS_COORDINATOR)

    entities = []
    for channel_id in device.capabilities.supplement_light_channels:
        entities.append(SupplementLightEntity(coordinator, channel_id))

    async_add_entities(entities)


class SupplementLightEntity(CoordinatorEntity, LightEntity):
    """White supplement light entity."""

    _attr_has_entity_name = True
    _attr_translation_key = "supplement_light"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_icon = "mdi:spotlight"

    def __init__(self, coordinator, channel_id: int) -> None:
        """Initialize."""
        super().__init__(coordinator)
        serial_no = slugify(coordinator.device.device_info.serial_no.lower())
        unique_id = f"{serial_no}_{channel_id}_supplement_light"
        self._attr_unique_id = unique_id
        self.entity_id = ENTITY_ID_FORMAT.format(unique_id)
        self._attr_device_info = coordinator.device.hass_device_info(channel_id)
        self._channel_id = channel_id

    @property
    def _light_data(self):
        """Return current supplement light data from coordinator."""
        return self.coordinator.data.get(f"supplement_light_{self._channel_id}")

    @property
    def is_on(self) -> bool | None:
        """Return True if light is on."""
        if self._light_data is None:
            return None
        return self._light_data.mode == "colorVuWhiteLight"

    @property
    def brightness(self) -> int | None:
        """Return brightness scaled to 0-255."""
        if self._light_data is None:
            return None
        return math.ceil(self._light_data.brightness * 255 / 100)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        if ATTR_BRIGHTNESS in kwargs:
            brightness_pct = max(1, round(kwargs[ATTR_BRIGHTNESS] * 100 / 255))
        elif self._light_data and self._light_data.brightness > 0:
            brightness_pct = self._light_data.brightness
        else:
            brightness_pct = 100
        await self.coordinator.device.set_supplement_light_state(
            self._channel_id, True, brightness_pct
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self.coordinator.device.set_supplement_light_state(self._channel_id, False)
        await self.coordinator.async_request_refresh()
