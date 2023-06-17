"""Platform for sensor integration."""

from __future__ import annotations
from .isapi import HDDInfo

from homeassistant.components.sensor import ENTITY_ID_FORMAT, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ALARM_SERVER_SENSOR_LABEL_FORMAT,
    DATA_ALARM_SERVER_HOST,
    DOMAIN,
    EVENTS_COORDINATOR,
    SECONDARY_COORDINATOR,
)

ALARM_SERVER_SETTINGS = {
    "protocolType": "Protocol",
    "ipAddress": "IP",
    "portNo": "Port",
    "url": "Path",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add diagnostic sensors for hikvision alarm server settings."""

    config = hass.data[DOMAIN][entry.entry_id]
    coordinator = config.get(SECONDARY_COORDINATOR)

    entities = []
    if coordinator:
        for key in ALARM_SERVER_SETTINGS:
            entities.append(AlarmServerSensor(coordinator, key))

    events_coordinator = config.get(EVENTS_COORDINATOR)
    if events_coordinator:
        for hdd in list(events_coordinator.isapi.device_info.storage):
            entities.append(HDDSensor(coordinator, hdd))

    async_add_entities(entities, True)


class AlarmServerSensor(CoordinatorEntity, SensorEntity):
    """Alarm Server settings sensor."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:ip-network"

    def __init__(self, coordinator, key: str) -> None:
        super().__init__(coordinator)
        isapi = coordinator.isapi
        self._attr_unique_id = (
            f"{isapi.device_info.serial_no}_{DATA_ALARM_SERVER_HOST}_{key}"
        )
        self.entity_id = ENTITY_ID_FORMAT.format(self.unique_id)
        self._attr_device_info = isapi.get_device_info()
        self._attr_name = ALARM_SERVER_SENSOR_LABEL_FORMAT.format(
            ALARM_SERVER_SETTINGS[key]
        )
        self.key = key

    @property
    def native_value(self) -> str | None:
        host = self.coordinator.data.get(DATA_ALARM_SERVER_HOST)
        return getattr(host, self.key) if host else None


class HDDSensor(CoordinatorEntity, SensorEntity):
    """HDD Status Sensor"""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:harddisk"

    def __init__(self, coordinator, hdd: HDDInfo) -> None:
        super().__init__(coordinator)
        isapi = coordinator.isapi
        self._attr_unique_id = (
            f"{isapi.device_info.serial_no}_{hdd.id}_{hdd.name}"
        )
        self.entity_id = ENTITY_ID_FORMAT.format(self.unique_id)
        self._attr_device_info = isapi.get_device_info()
        self._attr_name = f"HDD {hdd.id}"
        self.hdd = hdd

    @property
    def native_value(self) -> str | None:
        hdd = self.coordinator.isapi.get_storage_device_by_id(self.hdd.id)
        return str(hdd.status).upper() if hdd else None

    @property
    def extra_state_attributes(self):
        attrs = {}
        attrs["type"] = self.hdd.type
        attrs["capacity"] = self.hdd.capacity
        attrs["freespace"] = self.hdd.freespace
        return attrs
