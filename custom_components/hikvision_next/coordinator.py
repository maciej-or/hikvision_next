"""Coordinators"""

from __future__ import annotations

from datetime import timedelta
import logging

import async_timeout

from homeassistant.components.switch import ENTITY_ID_FORMAT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DATA_ALARM_SERVER_HOST, DOMAIN, HOLIDAY_MODE
from .isapi import ISAPI

SCAN_INTERVAL_EVENTS = timedelta(seconds=300)
SCAN_INTERVAL_HOLIDAYS = timedelta(minutes=60)

_LOGGER = logging.getLogger(__name__)


class EventsCoordinator(DataUpdateCoordinator):
    """Manage fetching events state from NVR or camera"""

    def __init__(self, hass: HomeAssistant, isapi: ISAPI) -> None:
        """Initialize"""
        self.isapi = isapi

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL_EVENTS,
        )

    async def _async_update_data(self):
        """Update data via ISAPI"""
        async with async_timeout.timeout(30):
            data = {}
            for camera in self.isapi.cameras:
                for event in camera.supported_events:
                    try:
                        entity_id = ENTITY_ID_FORMAT.format(event.unique_id)
                        data[entity_id] = await self.isapi.get_event_enabled_state(
                            event
                        )
                    except Exception as ex:  # pylint: disable=broad-except
                        self.isapi.handle_exception(
                            ex, f"Cannot fetch state for {event.id}"
                        )
            return data


class SecondaryCoordinator(DataUpdateCoordinator):
    """Manage fetching events state from NVR"""

    def __init__(self, hass: HomeAssistant, isapi: ISAPI) -> None:
        """Initialize"""
        self.isapi = isapi

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL_HOLIDAYS,
        )

    async def _async_update_data(self):
        """Update data via ISAPI"""
        async with async_timeout.timeout(20):
            data = {}
            try:
                if self.isapi.device_info.support_holiday_mode:
                    data[HOLIDAY_MODE] = await self.isapi.get_holiday_enabled_state()
            except Exception as ex:  # pylint: disable=broad-except
                self.isapi.handle_exception(
                    ex, f"Cannot fetch state for {HOLIDAY_MODE}"
                )
            try:
                if self.isapi.device_info.support_alarm_server:
                    alarm_server = await self.isapi.get_alarm_server()
                    data[DATA_ALARM_SERVER_HOST] = alarm_server
            except Exception as ex:  # pylint: disable=broad-except
                self.isapi.handle_exception(
                    ex, f"Cannot fetch state for {DATA_ALARM_SERVER_HOST}"
                )
            return data
