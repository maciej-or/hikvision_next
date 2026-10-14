"""Platform for sensor integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import ENTITY_ID_FORMAT as BINARY_SENSOR_ENTITY_ID_FORMAT
from homeassistant.components.sensor import ENTITY_ID_FORMAT, SensorDeviceClass, SensorEntity
from homeassistant.const import PERCENTAGE, UnitOfElectricPotential, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from . import HikvisionConfigEntry
from .const import CONF_ALARM_SERVER_HOST, SECONDARY_COORDINATOR, SECURITY_COORDINATOR, SECURITY_HOST_STATUS
from .isapi import Peripheral, StorageInfo, Zone

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
    """Add diagnostic sensors for hikvision alarm server settings, storage items and zones."""

    device = entry.runtime_data
    coordinator = device.coordinators.get(SECONDARY_COORDINATOR)

    entities = []
    if coordinator:
        for key in NOTIFICATION_HOST_KEYS:
            entities.append(AlarmServerSensor(coordinator, key))

        for item in list(device.storage):
            entities.append(StorageSensor(coordinator, item))

    # Security control panel zone telemetry (battery/signal/temperature). Only wireless
    # zones report this -- wired zones don't return these fields at all, so use their
    # presence at setup time to decide whether these entities are worth creating.
    security_coordinator = device.coordinators.get(SECURITY_COORDINATOR)
    if security_coordinator:
        for zone in device.zones:
            if zone.charge_value is not None:
                entities.append(ZoneBatterySensor(security_coordinator, zone))
            if zone.signal is not None:
                entities.append(ZoneSignalSensor(security_coordinator, zone))
            if zone.temperature is not None:
                entities.append(ZoneTemperatureSensor(security_coordinator, zone))
            if zone.humidity is not None:
                entities.append(ZoneHumiditySensor(security_coordinator, zone))

    # Security control panel host-level status (backup battery, fault count, IP address)
    if device.device_info.is_security_panel:
        entities.append(PanelIPAddressSensor(device))
    if security_coordinator and device.device_info.is_security_panel:
        entities.append(PanelBatterySensor(device, security_coordinator))
        entities.append(PanelBatteryVoltageSensor(device, security_coordinator))
        entities.append(PanelFaultCountSensor(device, security_coordinator))

    # Security control panel peripherals (keypads, sirens, remotes, repeaters, extension
    # modules) telemetry -- only for the fields each kind actually reports.
    if security_coordinator:
        for peripheral in device.peripherals:
            if peripheral.charge_value is not None:
                entities.append(PeripheralBatterySensor(security_coordinator, peripheral))
            if peripheral.signal is not None:
                entities.append(PeripheralSignalSensor(security_coordinator, peripheral))
            if peripheral.temperature is not None:
                entities.append(PeripheralTemperatureSensor(security_coordinator, peripheral))

    if entities:
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
        self.entity_id = ENTITY_ID_FORMAT.format(slugify(self.unique_id))
        self._attr_device_info = device.hass_device_info()
        self._attr_translation_key = f"notifications_host_{key}"
        self.key = key

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        host = self.coordinator.data.get(CONF_ALARM_SERVER_HOST)
        return host.get(self.key) if host else None


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
        self.entity_id = ENTITY_ID_FORMAT.format(slugify(self.unique_id))
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


class ZoneTelemetrySensor(CoordinatorEntity, SensorEntity):
    """Base class for a security control panel zone's telemetry (battery/signal/temperature)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _data_key: str = None

    def __init__(self, coordinator, zone: Zone, name_suffix: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        device = coordinator.device
        self._zone_data_key = BINARY_SENSOR_ENTITY_ID_FORMAT.format(zone.unique_id)
        self._attr_unique_id = f"{zone.unique_id}_{name_suffix}"
        self.entity_id = ENTITY_ID_FORMAT.format(slugify(self.unique_id))
        self._attr_device_info = device.zone_device_info(zone)
        self._attr_translation_key = f"zone_{name_suffix}"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self.coordinator.data.get(self._zone_data_key)
        return data.get(self._data_key) if data else None


class ZoneBatterySensor(ZoneTelemetrySensor):
    """Zone detector battery level."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _data_key = "charge_value"

    def __init__(self, coordinator, zone: Zone) -> None:
        """Initialize."""
        super().__init__(coordinator, zone, "battery")


class ZoneSignalSensor(ZoneTelemetrySensor):
    """Zone detector RF signal quality."""

    _attr_icon = "mdi:signal"
    _data_key = "signal"

    def __init__(self, coordinator, zone: Zone) -> None:
        """Initialize."""
        super().__init__(coordinator, zone, "signal")


class ZoneTemperatureSensor(ZoneTelemetrySensor):
    """Zone detector reported temperature.

    Unlike battery/signal (device-health diagnostics), this is an actual environmental
    measurement -- shown as a regular sensor alongside the zone's own state instead of
    tucked into the Diagnostic section.
    """

    _attr_entity_category = None
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _data_key = "temperature"

    def __init__(self, coordinator, zone: Zone) -> None:
        """Initialize."""
        super().__init__(coordinator, zone, "temperature")


class ZoneHumiditySensor(ZoneTelemetrySensor):
    """Zone humidity detector reading. A regular sensor, same reasoning as temperature above."""

    _attr_entity_category = None
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _data_key = "humidity"

    def __init__(self, coordinator, zone: Zone) -> None:
        """Initialize."""
        super().__init__(coordinator, zone, "humidity")


class PanelHostStatusSensor(CoordinatorEntity, SensorEntity):
    """Base class for a security control panel's host-level status sensor (backup battery, faults)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _data_key: str = None

    def __init__(self, device, coordinator, name_suffix: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{device.device_info.serial_no}_{name_suffix}"
        self.entity_id = ENTITY_ID_FORMAT.format(slugify(self.unique_id))
        self._attr_device_info = device.hass_device_info()
        self._attr_translation_key = f"panel_{name_suffix}"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self.coordinator.data.get(SECURITY_HOST_STATUS)
        return data.get(self._data_key) if data else None


class PanelBatterySensor(PanelHostStatusSensor):
    """Security control panel backup battery level."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _data_key = "battery_percent"

    def __init__(self, device, coordinator) -> None:
        """Initialize."""
        super().__init__(device, coordinator, "battery")

    @property
    def extra_state_attributes(self):
        """Return extra attributes."""
        data = self.coordinator.data.get(SECURITY_HOST_STATUS) or {}
        return {"status": data.get("battery_status")}


class PanelBatteryVoltageSensor(PanelHostStatusSensor):
    """Security control panel backup battery voltage."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _data_key = "battery_voltage"

    def __init__(self, device, coordinator) -> None:
        """Initialize."""
        super().__init__(device, coordinator, "battery_voltage")


class PanelFaultCountSensor(PanelHostStatusSensor):
    """Number of active faults currently reported by the security control panel."""

    _attr_icon = "mdi:alert-circle-outline"
    _data_key = "fault_count"

    def __init__(self, device, coordinator) -> None:
        """Initialize."""
        super().__init__(device, coordinator, "fault_count")


class PanelIPAddressSensor(SensorEntity):
    """Security control panel IP address. Static -- not tied to a coordinator."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:ip-network"
    _attr_translation_key = "panel_ip_address"

    def __init__(self, device) -> None:
        """Initialize."""
        self._attr_unique_id = f"{device.device_info.serial_no}_ip_address"
        self.entity_id = ENTITY_ID_FORMAT.format(slugify(self.unique_id))
        self._attr_device_info = device.hass_device_info()
        self._attr_native_value = device.device_info.ip_address


class PeripheralTelemetrySensor(CoordinatorEntity, SensorEntity):
    """Base class for a security control panel peripheral's telemetry (battery/signal/temperature)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _data_key: str = None

    def __init__(self, coordinator, peripheral: Peripheral, name_suffix: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        device = coordinator.device
        self._peripheral_unique_id = peripheral.unique_id
        self._attr_unique_id = f"{peripheral.unique_id}_{name_suffix}"
        self.entity_id = ENTITY_ID_FORMAT.format(slugify(self.unique_id))
        self._attr_device_info = device.peripheral_device_info(peripheral)
        self._attr_translation_key = f"peripheral_{name_suffix}"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self.coordinator.data.get(self._peripheral_unique_id)
        return data.get(self._data_key) if data else None


class PeripheralBatterySensor(PeripheralTelemetrySensor):
    """Peripheral battery level."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _data_key = "charge_value"

    def __init__(self, coordinator, peripheral: Peripheral) -> None:
        """Initialize."""
        super().__init__(coordinator, peripheral, "battery")


class PeripheralSignalSensor(PeripheralTelemetrySensor):
    """Peripheral RF signal quality."""

    _attr_icon = "mdi:signal"
    _data_key = "signal"

    def __init__(self, coordinator, peripheral: Peripheral) -> None:
        """Initialize."""
        super().__init__(coordinator, peripheral, "signal")


class PeripheralTemperatureSensor(PeripheralTelemetrySensor):
    """Peripheral reported temperature."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _data_key = "temperature"

    def __init__(self, coordinator, peripheral: Peripheral) -> None:
        """Initialize."""
        super().__init__(coordinator, peripheral, "temperature")
