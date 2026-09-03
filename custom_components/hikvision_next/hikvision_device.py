"ISAPI client for Home Assistant integration."

import logging
from typing import Any

import httpx

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util import slugify

from .const import (
    ALARM_SERVER_PATH,
    CONF_ALARM_SERVER_HOST,
    CONF_SET_ALARM_SERVER,
    DOMAIN,
    EVENTS,
    EVENTS_COORDINATOR,
    RTSP_PORT_FORCED,
    SECONDARY_COORDINATOR,
)
from .coordinator import EventsCoordinator, SecondaryCoordinator
from .isapi import (
    EventInfo,
    IPCamera,
    ISAPIClient,
    ISAPIForbiddenError,
    ISAPIUnauthorizedError,
)
from .isapi.const import EVENT_IO

_LOGGER = logging.getLogger(__name__)


class HikvisionDevice(ISAPIClient):
    """Hikvision device for Home Assistant integration."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize device."""

        config = entry.data if entry else data
        self.entry = entry
        self.hass = hass
        self.auth_token_expired = False
        self.control_alarm_server_host = config[CONF_SET_ALARM_SERVER]
        self.alarm_server_host = config[CONF_ALARM_SERVER_HOST]

        # init ISAPI client
        host = config[CONF_HOST]
        username = config[CONF_USERNAME]
        password = config[CONF_PASSWORD]
        verify_ssl = config.get(CONF_VERIFY_SSL, True)
        rtsp_port_forced = config.get(RTSP_PORT_FORCED, None)
        session = get_async_client(hass, verify_ssl)
        super().__init__(host, username, password, verify_ssl, rtsp_port_forced, session)

        self.events_info: list[EventInfo] = []

    async def init_coordinators(self):
        """Initialize coordinators."""

        # init events supported by integration
        self.events_info = self.get_device_event_capabilities()
        for camera in self.cameras:
            camera.events_info = self.get_device_event_capabilities(camera.id)

        # create coordinators
        self.coordinators = {}
        self.coordinators[EVENTS_COORDINATOR] = EventsCoordinator(self.hass, self)
        if (
            self.capabilities.support_holiday_mode
            or self.capabilities.support_alarm_server
            or self.capabilities.storage
        ):
            self.coordinators[SECONDARY_COORDINATOR] = SecondaryCoordinator(self.hass, self)

        if self.control_alarm_server_host and self.capabilities.support_alarm_server:
            await self.set_alarm_server(self.alarm_server_host, ALARM_SERVER_PATH)

        # first data fetch
        for coordinator in self.coordinators.values():
            await coordinator.async_config_entry_first_refresh()

    def hass_device_info(self, camera_id: int = 0) -> DeviceInfo:
        """Return Home Assistant entity device information."""
        if camera_id == 0:
            return DeviceInfo(
                manufacturer=self.device_info.manufacturer,
                identifiers={(DOMAIN, self.device_info.serial_no)},
                connections={(dr.CONNECTION_NETWORK_MAC, self.device_info.mac_address)},
                model=self.device_info.model,
                name=self.device_info.name,
                sw_version=self.device_info.firmware,
            )
        else:
            camera_info = self.get_camera_by_id(camera_id)
            is_ip_camera = isinstance(camera_info, IPCamera)

            device_info = DeviceInfo(
                manufacturer=self.device_info.manufacturer,
                identifiers={(DOMAIN, camera_info.serial_no)},
                model=camera_info.model,
                name=camera_info.name,
                sw_version=camera_info.firmware if is_ip_camera else "Unknown",
            )

            if self.device_info.is_nvr:
                self._link_to_nvr(device_info)

            return device_info

    def _link_to_nvr(self, device_info: DeviceInfo) -> None:
        """Link a camera device to the NVR it belongs to.

        Home Assistant 2026.9 removed `via_device` from DeviceInfo and made the device registry
        raise on it, so on those cores the key must be absent rather than None: the registry
        reports it as soon as it is passed, whatever its value. Its replacement `via_device_id`,
        and the lookup needed to fill it, only arrived in 2026.8, so both spellings are kept
        while older cores are supported.
        """
        registry = dr.async_get(self.hass)
        identifier = (DOMAIN, self.device_info.serial_no)

        if hasattr(registry, "async_get_device_by_identifier"):
            # async_setup_entry registers the NVR before it forwards the platforms, so the
            # lookup finds it. Without a match the camera is still created, only unlinked.
            if self.entry:
                nvr_device = registry.async_get_device_by_identifier(identifier, self.entry.entry_id)
                if nvr_device:
                    device_info["via_device_id"] = nvr_device.id
        else:
            device_info["via_device"] = identifier

    def get_device_event_capabilities(
        self,
        camera_id: int | None = None,
    ) -> list[EventInfo]:
        """Get events info handled by integration (camera id:  NVR = None, camera > 0)."""
        events: list[EventInfo] = []
        events_by_unique_id: dict[str, EventInfo] = {}

        if camera_id is None:  # NVR
            integration_supported_events = [
                s for s in self.supported_events if (s.id in EVENTS and EVENTS[s.id].get("type") == EVENT_IO)
            ]
        else:  # Camera
            integration_supported_events = [
                s for s in self.supported_events if (s.channel_id == int(camera_id) and s.id in EVENTS)
            ]

        for event in integration_supported_events:
            # Build unique_id
            device_id_param = f"_{camera_id}" if camera_id else ""
            io_port_id_param = f"_{event.io_port_id}" if event.io_port_id != 0 else ""
            unique_id = f"{slugify(self.device_info.serial_no.lower())}{device_id_param}{io_port_id_param}_{event.id}"

            if EVENTS.get(event.id):
                notifications = list(event.notifications)
                disabled = "center" not in notifications  # Disable if not set Notify Surveillance Center
                event_info = EventInfo(
                    id=event.id,
                    channel_id=event.channel_id,
                    io_port_id=event.io_port_id,
                    unique_id=unique_id,
                    url=event.url,
                    is_proxy=event.is_proxy,
                    disabled=disabled,
                    notifications=notifications,
                )

                if existing := events_by_unique_id.get(unique_id):
                    merged_notifications = list(dict.fromkeys([*existing.notifications, *event_info.notifications]))
                    existing.notifications = merged_notifications
                    existing.disabled = "center" not in merged_notifications
                    if not existing.url and event_info.url:
                        existing.url = event_info.url
                    existing.is_proxy = existing.is_proxy or event_info.is_proxy
                    continue

                events_by_unique_id[unique_id] = event_info
                events.append(event_info)
        return events

    def handle_exception(self, ex: Exception, details: str = ""):
        """Handle common exceptions."""

        error = "Unexpected exception"

        if isinstance(ex, ISAPIUnauthorizedError):
            if not self.auth_token_expired:
                # after device reboot, authorization token may have expired
                self.auth_token_expired = True
                self._auth_method = None
                self._session = get_async_client(self.hass, self.verify_ssl)
                _LOGGER.warning("Unauthorized access to %s, started checking if token expired", self.host)
                return
            self.auth_token_expired = False
            self.entry.async_start_reauth(self.hass)
            error = "Unauthorized access"
        elif isinstance(ex, ISAPIForbiddenError):
            if getattr(ex, "suppressed", False):
                return
            error = "Forbidden access"
        elif isinstance(ex, (httpx.TimeoutException, httpx.ConnectTimeout)):
            error = "Timeout"
        elif isinstance(ex, (httpx.ConnectError, httpx.NetworkError)):
            error = "Connection error"

        _LOGGER.warning("%s | %s | %s | %s", error, self.host, details, ex)
