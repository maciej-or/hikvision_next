"""Platform for select integration."""

from __future__ import annotations

from homeassistant.components.select import ENTITY_ID_FORMAT, SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from . import HikvisionConfigEntry
from .const import EVENTS_COORDINATOR

DAY_NIGHT_FILTER_OPTIONS = ["day", "night", "auto", "schedule"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add hikvision_next select entities from a config_entry."""
    device = entry.runtime_data
    coordinator = device.coordinators.get(EVENTS_COORDINATOR)

    entities = []
    for channel_id in device.capabilities.day_night_filter_channels:
        entities.append(DayNightFilterEntity(coordinator, channel_id))

    async_add_entities(entities)


class DayNightFilterEntity(CoordinatorEntity, SelectEntity):
    """Day/night filter mode select entity."""

    _attr_has_entity_name = True
    _attr_translation_key = "day_night_filter"
    _attr_icon = "mdi:theme-light-dark"
    _attr_options = DAY_NIGHT_FILTER_OPTIONS

    def __init__(self, coordinator, channel_id: int) -> None:
        """Initialize."""
        super().__init__(coordinator)
        serial_no = slugify(coordinator.device.device_info.serial_no.lower())
        unique_id = f"{serial_no}_{channel_id}_day_night_filter"
        self._attr_unique_id = unique_id
        self.entity_id = ENTITY_ID_FORMAT.format(unique_id)
        self._attr_device_info = coordinator.device.hass_device_info(channel_id)
        self._channel_id = channel_id

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option."""
        return self.coordinator.data.get(f"day_night_filter_{self._channel_id}")

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self.coordinator.device.set_day_night_filter_type(self._channel_id, option)
        await self.coordinator.async_request_refresh()
