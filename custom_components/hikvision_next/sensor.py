"""Platform for sensor integration."""

from __future__ import annotations

from homeassistant.components.sensor import ENTITY_ID_FORMAT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ALARM_SERVER_SENSOR_LABEL_FORMAT,
    DATA_ALARM_SERVER_HOST,
    DOMAIN,
    SECONDARY_COORDINATOR,
)

ALARM_SERVER_SETTINGS = {
    "protocolType": "Protocol",
    "ipAddress": "IP",
    "portNo": "Port",
    "url": "Path",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add diagnostic sensors for hikvision alarm server settings."""

    config = hass.data[DOMAIN][entry.entry_id]
    coordinator = config.get(SECONDARY_COORDINATOR)

    entities = []
    if coordinator:
        for key in ALARM_SERVER_SETTINGS:
            entities.append(AlarmServerSensor(coordinator, key))

    async_add_entities(entities, True)


class AlarmServerSensor(CoordinatorEntity, Entity):
    """Alarm Server settings sensor."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:ip-network"

    def __init__(self, coordinator, key: str) -> None:
        super().__init__(coordinator)
        isapi = coordinator.isapi
        self._attr_unique_id = f"{isapi.serial_no}_{DATA_ALARM_SERVER_HOST}_{key}"
        self.entity_id = ENTITY_ID_FORMAT.format(self.unique_id)
        self._attr_device_info = isapi.device_info
        self._attr_name = ALARM_SERVER_SENSOR_LABEL_FORMAT.format(
            ALARM_SERVER_SETTINGS[key]
        )
        self.key = key

    async def async_update(self) -> None:
        """Get Alarm Server settings"""
        await super().async_update()
        self._attr_state = await self.async_get_state()

    async def async_get_state(self):
        """Get Alarm Server option value"""
        host = self.coordinator.data.get(DATA_ALARM_SERVER_HOST)
        return host.get(self.key) if host else None
