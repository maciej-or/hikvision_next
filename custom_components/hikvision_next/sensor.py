"""Platform for sensor integration."""

from __future__ import annotations

from homeassistant.components.sensor import ENTITY_ID_FORMAT, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HikvisionConfigEntry
from .const import CONF_ALARM_SERVER_HOST, SECONDARY_COORDINATOR
from .isapi import StorageInfo

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
    coordinator = device.coordinators.get(SECONDARY_COORDINATOR)

    entities = []
    if coordinator:
        for key in NOTIFICATION_HOST_KEYS:
            entities.append(AlarmServerSensor(coordinator, key))

        for item in list(device.storage):
            entities.append(StorageSensor(coordinator, item))

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
        return host.get(self.key)


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
