from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.httpx_client import get_async_client

from .const import (
    ALARM_SERVER_PATH,
    DATA_ALARM_SERVER_HOST,
    DATA_SET_ALARM_SERVER,
    DOMAIN,
    EVENTS_COORDINATOR,
    SECONDARY_COORDINATOR,
)
from .coordinator import EventsCoordinator, SecondaryCoordinator
from .isapi import ISAPI, IPCamera


class HikvisionDevice(ISAPI):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.control_alarm_server_host = entry.data[DATA_SET_ALARM_SERVER]
        self.alarm_server_host = entry.data[DATA_ALARM_SERVER_HOST]
        host = entry.data[CONF_HOST]
        username = entry.data[CONF_USERNAME]
        password = entry.data[CONF_PASSWORD]
        session = get_async_client(hass)
        super().__init__(host, username, password, session)

    async def init_coordinators(self):
        self.coordinators = {}
        self.coordinators[EVENTS_COORDINATOR] = EventsCoordinator(self.hass, self)
        if self.device_info.support_holiday_mode or self.device_info.support_alarm_server or self.device_info.storage:
            self.coordinators[SECONDARY_COORDINATOR] = SecondaryCoordinator(self.hass, self)

        if self.control_alarm_server_host and self.device_info.support_alarm_server:
            await self.set_alarm_server(self.alarm_server_host, ALARM_SERVER_PATH)

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
