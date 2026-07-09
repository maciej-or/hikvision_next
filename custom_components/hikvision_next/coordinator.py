"""Coordinators."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.components.switch import ENTITY_ID_FORMAT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import slugify

from .const import CONF_ALARM_SERVER_HOST, DOMAIN, HOLIDAY_MODE
from .sdk.video_intercom import IntercomCallState

SCAN_INTERVAL_EVENTS = timedelta(seconds=120)
SCAN_INTERVAL_HOLIDAYS = timedelta(minutes=60)

_LOGGER = logging.getLogger(__name__)


class EventsCoordinator(DataUpdateCoordinator):
    """Manage fetching events state from NVR or camera."""

    def __init__(self, hass: HomeAssistant, device) -> None:
        """Initialize."""
        self.device = device

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL_EVENTS,
        )

    async def _async_update_data(self):
        """Update data via ISAPI."""
        data = {}

        # Get camera event status
        for camera in self.device.cameras:
            for event in camera.events_info:
                if event.disabled or not event.url:
                    continue
                try:
                    _id = ENTITY_ID_FORMAT.format(event.unique_id)
                    data[_id] = await self.device.get_event_enabled_state(event)
                except Exception as ex:  # pylint: disable=broad-except
                    self.device.handle_exception(ex, f"Cannot fetch state for {event.id}")

        # Get NVR event status
        for event in self.device.events_info:
            if event.disabled or not event.url:
                continue
            try:
                _id = ENTITY_ID_FORMAT.format(event.unique_id)
                data[_id] = await self.device.get_event_enabled_state(event)
            except Exception as ex:  # pylint: disable=broad-except
                self.device.handle_exception(ex, f"Cannot fetch state for {event.id}")

        # Get output port(s) status
        for i in range(1, self.device.capabilities.output_ports + 1):
            try:
                _id = ENTITY_ID_FORMAT.format(f"{slugify(self.device.device_info.serial_no.lower())}_{i}_alarm_output")
                data[_id] = await self.device.get_io_port_status("output", i)
            except Exception as ex:  # pylint: disable=broad-except
                self.device.handle_exception(ex, f"Cannot fetch state for alarm output {i}")

        # Refresh HDD data
        try:
            if self.device.capabilities.support_storage:
                self.device.storage = await self.device.get_storage_devices()
        except Exception as ex:  # pylint: disable=broad-except
            self.device.handle_exception(ex, "Cannot fetch storage state")


        if self.device.auth_token_expired:
            self.device.auth_token_expired = False

        return data


class SecondaryCoordinator(DataUpdateCoordinator):
    """Manage fetching events state from NVR."""

    def __init__(self, hass: HomeAssistant, device) -> None:
        """Initialize."""
        self.device = device

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL_HOLIDAYS,
        )

    async def _async_update_data(self):
        """Update data via ISAPI."""
        data = {}
        try:
            if self.device.capabilities.support_holiday_mode:
                data[HOLIDAY_MODE] = await self.device.get_holiday_enabled_state()
        except Exception as ex:  # pylint: disable=broad-except
            self.device.handle_exception(ex, f"Cannot fetch state for {HOLIDAY_MODE}")
        try:
            if self.device.capabilities.support_alarm_server:
                alarm_server = await self.device.get_alarm_server()
                data[CONF_ALARM_SERVER_HOST] = {
                    "protocol_type": alarm_server.protocol_type,
                    "address": alarm_server.ip_address or alarm_server.host_name,
                    "port_no": alarm_server.port_no,
                    "path": alarm_server.url,

                }
        except Exception as ex:  # pylint: disable=broad-except
            self.device.handle_exception(ex, f"Cannot fetch state for {CONF_ALARM_SERVER_HOST}")
        return data


class ConnectionStatusCoordinator(DataUpdateCoordinator):
    """Base coordinator for event-driven SDK/HTTP connection health."""

    def __init__(
        self,
        hass: HomeAssistant,
        device,
        *,
        coordinator_name: str,
    ) -> None:
        self.device = device
        self._connected = False
        self._reason: str | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=coordinator_name,
            update_interval=None,  # purely event driven
        )

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def reason(self) -> str | None:
        return self._reason

    async def async_set_connected(self, connected: bool, reason: str | None = None):
        """Update connection state and notify listeners."""
        if self._connected != connected or self._reason != reason:
            self._connected = connected
            self._reason = reason
            self.async_set_updated_data({
                "connected": connected,
                "reason": reason,
            })


class SubscribeStatusCoordinator(ConnectionStatusCoordinator):
    """Coordinator for SDK alarm channel / subscribeEvent connection health."""

    def __init__(self, hass: HomeAssistant, device) -> None:
        super().__init__(
            hass,
            device,
            coordinator_name=f"{DOMAIN}_subscribe_{device.device_info.serial_no}",
        )


class IntercomStatusCoordinator(ConnectionStatusCoordinator):
    """Coordinator for VideoIntercomRemoteConfig session health."""

    def __init__(self, hass: HomeAssistant, device) -> None:
        self._call_state = IntercomCallState.IDLE
        super().__init__(
            hass,
            device,
            coordinator_name=f"{DOMAIN}_intercom_{device.device_info.serial_no}",
        )

    @property
    def call_state(self) -> IntercomCallState:
        return self._call_state

    async def async_set_call_state(self, call_state: IntercomCallState) -> None:
        """Update intercom call state and notify listeners."""
        if self._call_state != call_state:
            self._call_state = call_state
            self.async_set_updated_data(self._snapshot())

    def _snapshot(self) -> dict:
        return {
            "connected": self._connected,
            "reason": self._reason,
            "call_state": int(self._call_state),
        }

    async def async_set_connected(self, connected: bool, reason: str | None = None):
        """Update connection state and notify listeners."""
        if self._connected != connected or self._reason != reason:
            self._connected = connected
            self._reason = reason
            if not connected:
                self._call_state = IntercomCallState.IDLE
            self.async_set_updated_data(self._snapshot())
