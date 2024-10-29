from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util import slugify

from .api.models import EventInfo
from .const import (
    ALARM_SERVER_PATH,
    CONNECTION_TYPE_DIRECT,
    CONNECTION_TYPE_PROXIED,
    DATA_ALARM_SERVER_HOST,
    DATA_SET_ALARM_SERVER,
    DOMAIN,
    EVENT_BASIC,
    EVENT_IO,
    EVENT_PIR,
    EVENTS,
    EVENTS_COORDINATOR,
    SECONDARY_COORDINATOR,
)
from .coordinator import EventsCoordinator, SecondaryCoordinator
from .isapi import ISAPI, IPCamera


class HikvisionDevice(ISAPI):
    """Hikvision device for Home Assistant integration."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize device."""

        self.hass = hass
        self.control_alarm_server_host = entry.data[DATA_SET_ALARM_SERVER]
        self.alarm_server_host = entry.data[DATA_ALARM_SERVER_HOST]

        # init ISAPI client
        host = entry.data[CONF_HOST]
        username = entry.data[CONF_USERNAME]
        password = entry.data[CONF_PASSWORD]
        session = get_async_client(hass)
        super().__init__(host, username, password, session)

        self.events_info: list[EventInfo] = []

    async def init_coordinators(self):
        """Initialize coordinators."""

        # init events supported by integration
        self.events_info = await self.get_device_event_capabilities()
        for camera in self.cameras:
            camera.events_info = await self.get_device_event_capabilities(camera.id, camera.connection_type)

        # create coordinators
        self.coordinators = {}
        self.coordinators[EVENTS_COORDINATOR] = EventsCoordinator(self.hass, self)
        if self.capabilities.support_holiday_mode or self.device_info.support_alarm_server or self.capabilities.storage:
            self.coordinators[SECONDARY_COORDINATOR] = SecondaryCoordinator(self.hass, self)

        if self.control_alarm_server_host and self.device_info.support_alarm_server:
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

            return DeviceInfo(
                manufacturer=self.device_info.manufacturer,
                identifiers={(DOMAIN, camera_info.serial_no)},
                model=camera_info.model,
                name=camera_info.name,
                sw_version=camera_info.firmware if is_ip_camera else "Unknown",
                via_device=(DOMAIN, self.device_info.serial_no) if self.device_info.is_nvr else None,
            )

    async def get_device_event_capabilities(
        self,
        camera_id: int | None = None,
        connection_type: str = CONNECTION_TYPE_DIRECT,
    ) -> list[EventInfo]:
        """Get events info handled by integration (camera id:  NVR = None, camera > 0)."""
        events = []

        if camera_id is None:  # NVR
            integration_supported_events = [
                s
                for s in self.supported_events
                if (s.id in EVENTS and EVENTS[s.id].get("type") == EVENT_IO)
            ]
        else:  # Camera
            integration_supported_events = [
                s for s in self.supported_events if (s.channel_id == int(camera_id) and s.id in EVENTS)
            ]

        for event in integration_supported_events:
            # Build unique_id
            device_id_param = f"_{camera_id}" if camera_id else ""
            io_port_id_param = f"_{event.io_port_id}" if event.io_port_id != 0 else ""
            unique_id = (
                f"{slugify(self.device_info.serial_no.lower())}{device_id_param}{io_port_id_param}_{event.id}"
            )

            if EVENTS.get(event.id):
                event_info = EventInfo(
                    id=event.id,
                    channel_id=event.channel_id,
                    io_port_id=event.io_port_id,
                    unique_id=unique_id,
                    url=self.get_event_url(event, connection_type),
                    disabled=("center" not in event.notifications),  # Disable if not set Notify Surveillance Center
                )
                events.append(event_info)
        return events

    def get_event_url(self, event: EventInfo, connection_type: str) -> str:
        """Get event ISAPI URL."""

        event_type = EVENTS[event.id]["type"]
        slug = EVENTS[event.id]["slug"]

        if event_type == EVENT_BASIC:
            if connection_type == CONNECTION_TYPE_PROXIED:
                # ISAPI/ContentMgmt/InputProxy/channels/{channel_id}/video/{event}
                url = f"ContentMgmt/InputProxy/channels/{event.channel_id}/video/{slug}"
            else:
                # ISAPI/System/Video/inputs/channels/{channel_id}/{event}
                url = f"System/Video/inputs/channels/{event.channel_id}/{slug}"

        elif event_type == EVENT_IO:
            if connection_type == CONNECTION_TYPE_PROXIED:
                # ISAPI/ContentMgmt/IOProxy/{slug}/{channel_id}
                url = f"ContentMgmt/IOProxy/{slug}/{event.io_port_id}"
            else:
                # ISAPI/System/IO/{slug}}/{channel_id}
                url = f"System/IO/{slug}/{event.io_port_id}"
        elif event_type == EVENT_PIR:
            # ISAPI/WLAlarm/PIR
            url = slug
        else:
            # ISAPI/Smart/{event}/{channel_id}
            url = f"Smart/{slug}/{event.channel_id}"
        return url
