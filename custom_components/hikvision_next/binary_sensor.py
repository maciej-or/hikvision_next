"""Platform for binary sensor integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import ENTITY_ID_FORMAT, BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from . import HikvisionConfigEntry
from .const import EVENTS, SECURITY_COORDINATOR, SECURITY_HOST_STATUS, ZONE_DEVICE_CLASS
from .hikvision_device import HikvisionDevice
from .isapi import EventInfo, Peripheral, Zone
from .isapi.const import EVENT_IO


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add binary sensors for hikvision events states."""

    device = entry.runtime_data

    entities = []

    # Video Events
    for camera in device.cameras:
        for event in camera.events_info:
            entities.append(EventBinarySensor(device, camera.id, event))

    # General Events
    for event in device.events_info:
        entities.append(EventBinarySensor(device, 0, event))

    # Security control panel zones and host-level status
    coordinator = device.coordinators.get(SECURITY_COORDINATOR)
    for zone in device.zones:
        entities.append(ZoneBinarySensor(device, zone, coordinator))
        # These are reported for every zone regardless of wired/wireless (confirmed against
        # real hardware), unlike battery/signal/temperature/is_via_repeater which are
        # wireless-only -- so, unlike those, they're created unconditionally here.
        entities.append(ZoneTamperSensor(device, coordinator, zone))
        entities.append(ZoneArmedSensor(device, coordinator, zone))
        entities.append(ZoneAlarmSensor(device, coordinator, zone))
        entities.append(ZoneStayAwaySensor(device, coordinator, zone))
        # Wireless-only (a wired zone never reports this field at all), so gated like
        # battery/signal/temperature instead of created unconditionally.
        if zone.is_via_repeater is not None:
            entities.append(ZoneViaRepeaterSensor(device, coordinator, zone))
    if coordinator and device.device_info.is_security_panel:
        entities.append(PanelACPowerSensor(device, coordinator))
        entities.append(PanelTamperSensor(device, coordinator))

    # Security control panel peripherals (keypads, sirens, remotes, repeaters, extension
    # modules) -- only create each diagnostic if the peripheral actually reports that field
    # (e.g. remotes/keyfobs don't report status/tamper at all).
    for peripheral in device.peripherals:
        if peripheral.tamper_evident is not None:
            entities.append(PeripheralTamperSensor(device, coordinator, peripheral))
        if peripheral.status is not None:
            entities.append(PeripheralOnlineSensor(device, coordinator, peripheral))
        if peripheral.charge is not None:
            entities.append(PeripheralBatteryLowSensor(device, coordinator, peripheral))
        if peripheral.mains_power is not None:
            entities.append(PeripheralMainsPowerSensor(device, coordinator, peripheral))

    async_add_entities(entities)


class EventBinarySensor(BinarySensorEntity):
    """Event detection sensor."""

    _attr_has_entity_name = True
    _attr_is_on = False

    def __init__(self, device: HikvisionDevice, device_id: int, event: EventInfo) -> None:
        """Initialize."""
        self.entity_id = ENTITY_ID_FORMAT.format(event.unique_id)
        self._attr_unique_id = self.entity_id
        self._attr_device_class = EVENTS[event.id]["device_class"]
        self._attr_device_info = device.hass_device_info(device_id)
        self._attr_entity_registry_enabled_default = not event.disabled

        # For multi-channel devices, add channel suffix to name for clarity
        if device.capabilities.is_multi_channel and event.channel_id > 0:
            base_label = EVENTS[event.id]["label"]
            self._attr_name = f"{base_label} (Ch. {event.channel_id})"
        else:
            self._attr_translation_key = event.id
            if event.id == EVENT_IO:
                self._attr_translation_placeholders = {"io_port_id": event.io_port_id}

class ZoneBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Security control panel zone sensor."""

    _attr_has_entity_name = True

    def __init__(self, device: HikvisionDevice, zone: Zone, coordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_id = ENTITY_ID_FORMAT.format(zone.unique_id)
        self._attr_unique_id = self.entity_id
        # No _attr_name: this is the zone's own device's main entity, so has_entity_name
        # collapses it to just the device (zone) name instead of duplicating it.
        self._attr_name = None
        self._attr_device_class = ZONE_DEVICE_CLASS.get(zone.detector_type, BinarySensorDeviceClass.SAFETY)
        self._attr_device_info = device.zone_device_info(zone)
        self.zone = zone

    @property
    def is_on(self) -> bool | None:
        """Return True if the zone is triggered (or, for door/window zones, open)."""
        data = self.coordinator.data.get(self.unique_id)
        return bool(data) and data.get("triggered", False)

    @property
    def extra_state_attributes(self):
        """Return extra attributes.

        Only fields that aren't already their own diagnostic entity -- bypassed, tamper,
        armed, alarm, stay-arming bypass, battery and via-repeater all have dedicated
        entities below (so they show up in the device's Diagnostic section in the UI instead
        of being buried in this entity's attributes). "magnet_open_status" stays
        attribute-only: confirmed against real hardware to be unreliable on some wireless
        zones (stuck at a fixed value), so it's kept purely informational.
        """
        data = self.coordinator.data.get(self.unique_id) or {}
        return {
            "status": data.get("status"),
            "battery_low": data.get("charge") == "lowPower",
            "magnet_open_status": data.get("magnet_open_status"),
        }


class ZoneDiagnosticBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Base class for a security control panel zone's binary diagnostic attribute.

    Reads from the same coordinator data entry as the zone's own ZoneBinarySensor (keyed by
    the zone's primary entity_id), just projecting a different field of it.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _data_key: str = None

    def __init__(self, device: HikvisionDevice, coordinator, zone: Zone, name_suffix: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._zone_data_key = ENTITY_ID_FORMAT.format(zone.unique_id)
        self._attr_unique_id = f"{zone.unique_id}_{name_suffix}"
        self.entity_id = ENTITY_ID_FORMAT.format(slugify(self._attr_unique_id))
        self._attr_device_info = device.zone_device_info(zone)
        self._attr_translation_key = f"zone_{name_suffix}"

    @property
    def is_on(self) -> bool | None:
        """Return the value for this diagnostic, from the zone's raw status."""
        data = self.coordinator.data.get(self._zone_data_key)
        return data.get(self._data_key) if data else None


class ZoneTamperSensor(ZoneDiagnosticBinarySensor):
    """Whether the zone's own case/cover is open (tamper)."""

    _attr_device_class = BinarySensorDeviceClass.TAMPER
    _data_key = "tamper_evident"

    def __init__(self, device: HikvisionDevice, coordinator, zone: Zone) -> None:
        """Initialize."""
        super().__init__(device, coordinator, zone, "tamper")


class ZoneArmedSensor(ZoneDiagnosticBinarySensor):
    """Whether the zone is currently armed."""

    _attr_icon = "mdi:shield-check"
    _data_key = "armed"

    def __init__(self, device: HikvisionDevice, coordinator, zone: Zone) -> None:
        """Initialize."""
        super().__init__(device, coordinator, zone, "armed")


class ZoneAlarmSensor(ZoneDiagnosticBinarySensor):
    """Whether the zone currently has an alarm condition triggered."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _data_key = "alarm"

    def __init__(self, device: HikvisionDevice, coordinator, zone: Zone) -> None:
        """Initialize."""
        super().__init__(device, coordinator, zone, "alarm")


class ZoneStayAwaySensor(ZoneDiagnosticBinarySensor):
    """Whether stay-arming bypass is enabled for the zone."""

    _attr_icon = "mdi:shield-half-full"
    _data_key = "stay_away"

    def __init__(self, device: HikvisionDevice, coordinator, zone: Zone) -> None:
        """Initialize."""
        super().__init__(device, coordinator, zone, "stay_away")


class ZoneViaRepeaterSensor(ZoneDiagnosticBinarySensor):
    """Whether the wireless zone's signal is relayed via a repeater."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _data_key = "is_via_repeater"

    def __init__(self, device: HikvisionDevice, coordinator, zone: Zone) -> None:
        """Initialize."""
        super().__init__(device, coordinator, zone, "is_via_repeater")


class PanelHostStatusBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Base class for a security control panel's host-level status (not tied to any zone)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _data_key: str = None

    def __init__(self, device: HikvisionDevice, coordinator, name_suffix: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        unique_id = f"{device.device_info.serial_no}_{name_suffix}"
        self.entity_id = ENTITY_ID_FORMAT.format(slugify(unique_id))
        self._attr_unique_id = self.entity_id
        self._attr_device_info = device.hass_device_info()
        self._attr_translation_key = f"panel_{name_suffix}"

    @property
    def is_on(self) -> bool | None:
        """Return the host status value for this sensor."""
        data = self.coordinator.data.get(SECURITY_HOST_STATUS)
        return data.get(self._data_key) if data else None


class PanelACPowerSensor(PanelHostStatusBinarySensor):
    """Whether the panel's mains (AC) power is connected."""

    _attr_device_class = BinarySensorDeviceClass.POWER
    _data_key = "ac_connected"

    def __init__(self, device: HikvisionDevice, coordinator) -> None:
        """Initialize."""
        super().__init__(device, coordinator, "ac_power")


class PanelTamperSensor(PanelHostStatusBinarySensor):
    """Whether the panel's own case/cover is open (tamper)."""

    _attr_device_class = BinarySensorDeviceClass.TAMPER
    _data_key = "tamper_evident"

    def __init__(self, device: HikvisionDevice, coordinator) -> None:
        """Initialize."""
        super().__init__(device, coordinator, "tamper")


class PeripheralBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Base class for a security control panel peripheral's binary diagnostic attribute."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _data_key: str = None

    def __init__(self, device: HikvisionDevice, coordinator, peripheral: Peripheral, name_suffix: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{peripheral.unique_id}_{name_suffix}"
        self.entity_id = ENTITY_ID_FORMAT.format(slugify(self._attr_unique_id))
        self._attr_device_info = device.peripheral_device_info(peripheral)
        self._attr_translation_key = f"peripheral_{name_suffix}"
        self._peripheral_unique_id = peripheral.unique_id

    @property
    def is_on(self) -> bool | None:
        """Return the value for this diagnostic, from the peripheral's raw status."""
        data = self.coordinator.data.get(self._peripheral_unique_id)
        return data.get(self._data_key) if data else None


class PeripheralTamperSensor(PeripheralBinarySensor):
    """Whether the peripheral's own case/cover is open (tamper)."""

    _attr_device_class = BinarySensorDeviceClass.TAMPER
    _data_key = "tamper_evident"

    def __init__(self, device: HikvisionDevice, coordinator, peripheral: Peripheral) -> None:
        """Initialize."""
        super().__init__(device, coordinator, peripheral, "tamper")


class PeripheralOnlineSensor(PeripheralBinarySensor):
    """Whether the peripheral is currently online."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _data_key = "status"

    def __init__(self, device: HikvisionDevice, coordinator, peripheral: Peripheral) -> None:
        """Initialize."""
        super().__init__(device, coordinator, peripheral, "online")

    @property
    def is_on(self) -> bool | None:
        """Return True if the peripheral's reported status is "online"."""
        data = self.coordinator.data.get(self._peripheral_unique_id)
        status = data.get("status") if data else None
        return status == "online" if status is not None else None


class PeripheralBatteryLowSensor(PeripheralBinarySensor):
    """Whether the peripheral's battery is low."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _data_key = "charge"

    def __init__(self, device: HikvisionDevice, coordinator, peripheral: Peripheral) -> None:
        """Initialize."""
        super().__init__(device, coordinator, peripheral, "battery_low")

    @property
    def is_on(self) -> bool | None:
        """Return True if the peripheral's reported charge status is "lowPower"."""
        data = self.coordinator.data.get(self._peripheral_unique_id)
        charge = data.get("charge") if data else None
        return charge == "lowPower" if charge is not None else None


class PeripheralMainsPowerSensor(PeripheralBinarySensor):
    """Whether the peripheral has external/AC power connected (wireless sirens/repeaters)."""

    _attr_device_class = BinarySensorDeviceClass.POWER
    _data_key = "mains_power"

    def __init__(self, device: HikvisionDevice, coordinator, peripheral: Peripheral) -> None:
        """Initialize."""
        super().__init__(device, coordinator, peripheral, "mains_power")
