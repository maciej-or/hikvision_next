"""Platform for switch integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import ENTITY_ID_FORMAT as BINARY_SENSOR_ENTITY_ID_FORMAT
from homeassistant.components.switch import ENTITY_ID_FORMAT, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from . import HikvisionConfigEntry
from .const import EVENTS, EVENTS_COORDINATOR, HOLIDAY_MODE, SECONDARY_COORDINATOR, SECURITY_COORDINATOR
from .isapi import EventInfo, ISAPISetEventStateMutexError, Zone
from .isapi.const import EVENT_IO


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add hikvision_next entities from a config_entry."""

    device = entry.runtime_data
    entities = []

    # Security control panels have their own coordinator/platforms (alarm_control_panel,
    # zone binary_sensor) and none of the NVR/camera concepts below (event switches,
    # System/IO output ports, System/Holidays) apply to them.
    if not device.device_info.is_security_panel:
        events_coordinator = device.coordinators.get(EVENTS_COORDINATOR)
        secondary_coordinator = device.coordinators.get(SECONDARY_COORDINATOR)

        # Camera supported events
        for camera in device.cameras:
            for event in camera.events_info:
                entities.append(EventSwitch(camera.id, event, events_coordinator))

        # Device supported events
        for event in device.events_info:
            entities.append(EventSwitch(0, event, events_coordinator))

        # Output port switch
        for i in range(1, device.capabilities.output_ports + 1):
            entities.append(NVROutputSwitch(events_coordinator, i))

        # Holiday mode switch
        if device.capabilities.support_holiday_mode:
            entities.append(HolidaySwitch(secondary_coordinator))
    else:
        security_coordinator = device.coordinators.get(SECURITY_COORDINATOR)
        for zone in device.zones:
            entities.append(ZoneBypassSwitch(device, security_coordinator, zone))
            entities.append(ZoneChimeSwitch(device, zone))
            entities.append(ZoneSilentSwitch(device, zone))

    async_add_entities(entities)


class EventSwitch(CoordinatorEntity, SwitchEntity):
    """Detection events switch."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:eye-outline"

    def __init__(self, device_id: int, event: EventInfo, coordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_id = ENTITY_ID_FORMAT.format(event.unique_id)
        self._attr_unique_id = self.entity_id
        self._attr_device_info = coordinator.device.hass_device_info(device_id)
        self._attr_entity_registry_enabled_default = not event.disabled
        self.device_id = device_id
        self.event = event

        # For multi-channel devices, add channel suffix to name for clarity
        if coordinator.device.capabilities.is_multi_channel and event.channel_id > 0:
            base_label = EVENTS[event.id]["label"]
            self._attr_name = f"{base_label} Detection (Ch. {event.channel_id})"
        else:
            self._attr_translation_key = event.id
            if event.id == EVENT_IO:
                self._attr_translation_placeholders = {"io_port_id": event.io_port_id}

    @property
    def is_on(self) -> bool | None:
        """Return True if the binary sensor is on."""
        return self.coordinator.data.get(self.unique_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        try:
            await self.coordinator.device.set_event_enabled_state(self.device_id, self.event, True)
        except ISAPISetEventStateMutexError as ex:
            raise HomeAssistantError(ex.message)
        except Exception as ex:
            raise ex
        finally:
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        try:
            await self.coordinator.device.set_event_enabled_state(self.device_id, self.event, False)
        except Exception:
            raise
        finally:
            await self.coordinator.async_request_refresh()


class NVROutputSwitch(CoordinatorEntity, SwitchEntity):
    """Detection events switch."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:eye-outline"
    _attr_translation_key = "alarm_output"

    def __init__(self, coordinator, port_no: int) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_id = ENTITY_ID_FORMAT.format(
            f"{slugify(coordinator.device.device_info.serial_no.lower())}_{port_no}_alarm_output"
        )
        self._attr_unique_id = self.entity_id
        self._attr_device_info = coordinator.device.hass_device_info(0)
        self._attr_translation_placeholders = {"port_no": port_no}
        self._port_no = port_no

    @property
    def is_on(self) -> bool | None:
        """Turn on."""
        return self.coordinator.data.get(self.unique_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        try:
            await self.coordinator.device.set_output_port_state(self._port_no, True)
        except Exception as ex:
            raise ex
        finally:
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.device.set_output_port_state(self._port_no, False)
        except Exception as ex:
            raise ex
        finally:
            await self.coordinator.async_request_refresh()


class HolidaySwitch(CoordinatorEntity, SwitchEntity):
    """Holidays mode switch."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:palm-tree"
    _attr_translation_key = HOLIDAY_MODE

    def __init__(self, coordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{slugify(coordinator.device.device_info.serial_no.lower())}_{HOLIDAY_MODE}"
        self.entity_id = ENTITY_ID_FORMAT.format(self.unique_id)
        self._attr_device_info = coordinator.device.hass_device_info()

    @property
    def is_on(self) -> bool | None:
        """Return True if the binary sensor is on."""
        return self.coordinator.data.get(HOLIDAY_MODE)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        await self.coordinator.device.set_holiday_enabled_state(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self.coordinator.device.set_holiday_enabled_state(False)
        await self.coordinator.async_request_refresh()


class ZoneBypassSwitch(CoordinatorEntity, SwitchEntity):
    """Whether a security control panel zone is bypassed (excluded from the next arming
    cycle) -- reflects and drives the same "bypassed" field already polled for the zone's
    diagnostic binary_sensor, so it stays in sync automatically.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:shield-off-outline"
    _attr_translation_key = "zone_bypassed"

    def __init__(self, device, coordinator, zone: Zone) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{zone.unique_id}_bypassed"
        self.entity_id = ENTITY_ID_FORMAT.format(slugify(self._attr_unique_id))
        self._attr_device_info = device.zone_device_info(zone)
        self._zone_data_key = BINARY_SENSOR_ENTITY_ID_FORMAT.format(zone.unique_id)
        self.zone_id = zone.id

    @property
    def is_on(self) -> bool | None:
        """Return True if the zone is currently bypassed."""
        data = self.coordinator.data.get(self._zone_data_key)
        return data.get("bypassed") if data else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Bypass the zone."""
        await self.coordinator.device.bypass_zone(self.zone_id)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Recover (un-bypass) the zone."""
        await self.coordinator.device.recover_bypass_zone(self.zone_id)
        await self.coordinator.async_request_refresh()


class ZoneConfigSwitch(SwitchEntity):
    """Base class for a simple boolean zone setting that isn't part of routine status
    polling (SecurityCP/status/zones doesn't report it) -- only the Configuration endpoint
    does, and it only changes via explicit action (from here or the panel's own app/keypad),
    so state is tracked locally rather than through the SecurityCoordinator.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, device, zone: Zone, name_suffix: str, initial_state: bool | None) -> None:
        """Initialize."""
        self._attr_unique_id = f"{zone.unique_id}_{name_suffix}"
        self.entity_id = ENTITY_ID_FORMAT.format(slugify(self._attr_unique_id))
        self._attr_device_info = device.zone_device_info(zone)
        self._attr_translation_key = f"zone_{name_suffix}"
        self._attr_is_on = initial_state
        self._device = device
        self.zone_id = zone.id

    async def _async_call_api(self, state: bool) -> None:
        raise NotImplementedError

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        await self._async_call_api(True)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self._async_call_api(False)
        self._attr_is_on = False
        self.async_write_ha_state()


class ZoneChimeSwitch(ZoneConfigSwitch):
    """Whether a doorbell chime sounds when the zone opens."""

    _attr_icon = "mdi:bell-outline"

    def __init__(self, device, zone: Zone) -> None:
        """Initialize."""
        super().__init__(device, zone, "chime", zone.chime_enabled)

    async def _async_call_api(self, state: bool) -> None:
        await self._device.set_zone_chime_enabled(self.zone_id, state)


class ZoneSilentSwitch(ZoneConfigSwitch):
    """Whether the siren is muted for this zone specifically."""

    _attr_icon = "mdi:volume-off"

    def __init__(self, device, zone: Zone) -> None:
        """Initialize."""
        super().__init__(device, zone, "silent", zone.silent_enabled)

    async def _async_call_api(self, state: bool) -> None:
        await self._device.set_zone_silent_enabled(self.zone_id, state)
