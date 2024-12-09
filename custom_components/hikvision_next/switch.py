"""Platform for switch integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import ENTITY_ID_FORMAT, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from . import HikvisionConfigEntry
from .const import EVENTS_COORDINATOR, HOLIDAY_MODE, SECONDARY_COORDINATOR
from .isapi import EventInfo, ISAPISetEventStateMutexError
from .isapi.const import EVENT_IO


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add hikvision_next entities from a config_entry."""

    device = entry.runtime_data
    events_coordinator = device.coordinators.get(EVENTS_COORDINATOR)
    secondary_coordinator = device.coordinators.get(SECONDARY_COORDINATOR)

    entities = []

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
        self._attr_translation_key = event.id
        if event.id == EVENT_IO:
            self._attr_translation_placeholders = {"io_port_id": event.io_port_id}
        self._attr_entity_registry_enabled_default = not event.disabled
        self.device_id = device_id
        self.event = event

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
