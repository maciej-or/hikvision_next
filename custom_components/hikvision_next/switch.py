"""Platform for switch integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import ENTITY_ID_FORMAT, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    DOMAIN,
    EVENT_SWITCH_LABEL_FORMAT,
    EVENTS,
    EVENTS_COORDINATOR,
    HOLIDAY_MODE,
    HOLIDAY_MODE_SWITCH_LABEL,
    SECONDARY_COORDINATOR,
)
from .isapi import EventInfo


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add hikvision_next entities from a config_entry."""

    config = hass.data[DOMAIN][entry.entry_id]
    events_coordinator = config[EVENTS_COORDINATOR]
    secondary_coordinator = config.get(SECONDARY_COORDINATOR)

    entities = []
    for camera in events_coordinator.isapi.cameras:
        for event in camera.supported_events:
            entities.append(CameraEventSwitch(camera, event, events_coordinator))

    if events_coordinator.isapi.device_info.is_nvr:
        for event in events_coordinator.isapi.device_info.supported_events:
            entities.append(NVREventSwitch(event, events_coordinator))

        for i in range(1, events_coordinator.isapi.device_info.output_ports + 1):
            entities.append(NVROutputSwitch(events_coordinator, i))

    if secondary_coordinator.isapi.device_info.support_holiday_mode:
        entities.append(HolidaySwitch(secondary_coordinator))

    async_add_entities(entities)


class CameraEventSwitch(CoordinatorEntity, SwitchEntity):
    """Detection events switch."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:eye-outline"

    def __init__(self, camera, event: EventInfo, coordinator) -> None:
        super().__init__(coordinator)
        self.entity_id = ENTITY_ID_FORMAT.format(event.unique_id)
        self._attr_unique_id = self.entity_id
        self._attr_device_info = coordinator.isapi.get_device_info(camera.id)
        self._attr_name = EVENT_SWITCH_LABEL_FORMAT.format(EVENTS[event.id]["label"])
        self.camera = camera
        self.event = event

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get(self.entity_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.isapi.set_event_enabled_state(self.camera.id, self.event, True)
        except Exception as ex:
            raise ex
        finally:
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.isapi.set_event_enabled_state(self.camera.id, self.event, False)
        except Exception as ex:
            raise ex
        finally:
            await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self):
        attrs = {}
        attrs["notify_HA"] = True if "center" in self.event.notifiers else False
        return attrs


class NVREventSwitch(CoordinatorEntity, SwitchEntity):
    """Detection events switch."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:eye-outline"

    def __init__(self, event: EventInfo, coordinator) -> None:
        super().__init__(coordinator)
        self.entity_id = ENTITY_ID_FORMAT.format(event.unique_id)
        self._attr_unique_id = self.entity_id
        self._attr_device_info = coordinator.isapi.get_device_info(0)
        self._attr_name = EVENT_SWITCH_LABEL_FORMAT.format(f"{EVENTS[event.id]['label']} {event.channel_id}")
        self.event = event

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get(self.entity_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.isapi.set_event_enabled_state(self.event.channel_id, self.event, True)
        except Exception as ex:
            raise ex
        finally:
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.isapi.set_event_enabled_state(self.event.channel_id, self.event, False)
        except Exception as ex:
            raise ex
        finally:
            await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self):
        attrs = {}
        attrs["notify_HA"] = True if "center" in self.event.notifiers else False
        return attrs


class NVROutputSwitch(CoordinatorEntity, SwitchEntity):
    """Detection events switch."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:eye-outline"

    def __init__(self, coordinator, port_no: int) -> None:
        super().__init__(coordinator)
        self.entity_id = ENTITY_ID_FORMAT.format(
            f"{slugify(coordinator.isapi.device_info.serial_no.lower())}_{port_no}_alarm_output"
        )
        self._attr_unique_id = self.entity_id
        self._attr_device_info = coordinator.isapi.get_device_info(0)
        self._attr_name = f"Alarm Output {port_no}"
        self._port_no = port_no

    @property
    def is_on(self) -> bool | None:
        return True if self.coordinator.data.get(self.entity_id) == "active" else False

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.isapi.set_port_state(self._port_no, True)
        except Exception as ex:
            raise ex
        finally:
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.isapi.set_port_state(self._port_no, False)
        except Exception as ex:
            raise ex
        finally:
            await self.coordinator.async_request_refresh()


class HolidaySwitch(CoordinatorEntity, SwitchEntity):
    """Holidays mode switch."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:palm-tree"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{slugify(coordinator.isapi.device_info.serial_no.lower())}_{HOLIDAY_MODE}"
        self.entity_id = ENTITY_ID_FORMAT.format(self.unique_id)
        self._attr_device_info = coordinator.isapi.get_device_info()
        self._attr_name = HOLIDAY_MODE_SWITCH_LABEL

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get(HOLIDAY_MODE)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.isapi.set_holiday_enabled_state(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.isapi.set_holiday_enabled_state(False)
        await self.coordinator.async_request_refresh()
