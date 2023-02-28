"""Hikvision ISAPI client"""

from __future__ import annotations

import asyncio
from collections import namedtuple
import datetime
from http import HTTPStatus
import logging
from typing import Any
from urllib.parse import urlparse

import attr
from hikvisionapi import AsyncClient
from httpx import HTTPStatusError, TimeoutException
from requests import HTTPError
import xmltodict

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import slugify

from .const import DEVICE_TYPE_NVR, DOMAIN, EVENT_BASIC, EVENTS, EVENTS_ALTERNATE_ID

Node = dict[str, Any]

_LOGGER = logging.getLogger(__name__)

GET = "get"
PUT = "put"

AlertInfo = namedtuple("AlertInfo", "channel_id event_id device_serial mac")


@attr.s
class EventInfo:
    """Event info of particular device"""

    id: str = attr.ib()
    device_info: DeviceInfo = attr.ib()
    unique_id: str = attr.ib()
    url: str = attr.ib()


class ISAPI:
    """hikvisionapi async client wrapper."""

    def __init__(self, host: str, username: str, password: str) -> None:
        self.isapi = AsyncClient(host, username, password, timeout=20)
        self.holidays_support = False
        self.alarm_server_support = False
        self.events_info: list[EventInfo] = []
        self.hw_info = {}
        self.serial_no = ""
        self.is_nvr = False
        self.device_name = ""

    async def get_hardware_info(self) -> dict[str, str]:
        """Get hardware info"""

        self.hw_info = await self.isapi.System.deviceInfo(method=GET)
        _LOGGER.debug("%s/ISAPI/System/deviceInfo %s", self.isapi.host, self.hw_info)
        self.serial_no = self.hw_info["DeviceInfo"]["serialNumber"]
        self.is_nvr = self.hw_info["DeviceInfo"]["deviceType"] == DEVICE_TYPE_NVR
        self.device_name = self.hw_info["DeviceInfo"]["deviceName"]
        return self.hw_info

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""

        info = self.hw_info["DeviceInfo"]
        return DeviceInfo(
            manufacturer="Hikvision",
            identifiers={(DOMAIN, self.serial_no)},
            connections={(dr.CONNECTION_NETWORK_MAC, info["macAddress"])},
            model=info["model"],
            name=self.device_name,
            sw_version=info["firmwareVersion"],
        )

    def get_nvr_channel_device_info(self, channel: Node) -> DeviceInfo | None:
        """Return device registry information for IP camera from NVR's subnet."""

        channel_descriptor = channel["sourceInputPortDescriptor"]
        serial_number = channel_descriptor.get("serialNumber")
        if not serial_number:
            serial_number = channel.get("devIndex")
        # serial_number is None for offline device
        if serial_number:
            return DeviceInfo(
                manufacturer="Hikvision",  # may be not accurate, no manufacturer info provided
                identifiers={(DOMAIN, serial_number)},
                model=channel_descriptor.get("model", "?"),
                name=channel["name"],
                sw_version=channel_descriptor.get("firmwareVersion", "?"),
                via_device=(DOMAIN, self.serial_no),
            )

    async def get_nvr_capabilities(self) -> None:
        """Get NVR capabilities."""

        events = []
        channels = await self.isapi.ContentMgmt.InputProxy.channels(method=GET)
        _LOGGER.debug(
            "%s/ISAPI/ContentMgmt/InputProxy/channels %s", self.isapi.host, channels
        )
        for channel in channels["InputProxyChannelList"]["InputProxyChannel"]:
            channel_info = self.get_nvr_channel_device_info(channel)
            if channel_info:
                events += await self._get_channel_events(channel_info, channel["id"])
        self.events_info = events

        try:
            await self.get_holiday_enabled_state()
            self.holidays_support = True
        except HTTPError as ex:
            _LOGGER.debug("%s/ISAPI/System/Holidays %s", self.isapi.host, ex)

        try:
            await self.get_alarm_server()
            self.alarm_server_support = True
        except HTTPError as ex:
            _LOGGER.debug(
                "%s/ISAPI/Event/notification/httpHosts %s", self.isapi.host, ex
            )

    async def get_ip_camera_capabilities(self) -> None:
        """Get standalone IP camera capabilities."""

        self.events_info = await self._get_channel_events(self.device_info, "1")

        try:
            await self.get_alarm_server()
            self.alarm_server_support = True
        except HTTPError as ex:
            _LOGGER.debug(
                "%s/ISAPI/Event/notification/httpHosts %s", self.isapi.host, ex
            )

    async def _get_camera_events_capabilities(self) -> list[str]:
        """Get available camera events."""

        data = await self.isapi.Event.capabilities(method=GET)
        _LOGGER.debug("%s/ISAPI/Event/capabilities %s", self.isapi.host, data)
        basic_events = [
            event_id.lower().replace("issupport", "")
            for event_id, is_supported in data["EventCap"].items()
            if str_to_bool(is_supported)
        ]

        data = await self.isapi.Smart.capabilities(method=GET)
        _LOGGER.debug("%s/ISAPI/Smart/capabilities %s", self.isapi.host, data)
        smart_events = [
            event_id.lower().replace("issupport", "")
            for event_id, is_supported in data["SmartCap"].items()
            if str_to_bool(is_supported)
        ]

        return basic_events + smart_events

    async def _get_channel_events_capabilities(self, channel_id: str) -> list[str]:
        """Get available NVR channel events."""

        data = await self.isapi.Event.channels[channel_id].capabilities(method=GET)
        _LOGGER.debug(
            "%s/ISAPI/Event/channels/%s/capabilities %s",
            self.isapi.host,
            channel_id,
            data,
        )
        event_types = data["ChannelEventCap"]["eventType"]["@opt"].lower()
        for alt_id, event_id in EVENTS_ALTERNATE_ID.items():
            event_types = event_types.replace(alt_id, event_id)
        return event_types.split(",")

    async def _get_channel_events(
        self, channel_info: DeviceInfo, channel_id: str
    ) -> list[EventInfo]:
        """Get available channel events."""

        supported_events = []
        if self.is_nvr:
            supported_events = await self._get_channel_events_capabilities(channel_id)
            # videoloss is not listed but I assume any NVR supports it
            supported_events.append("videoloss")
        else:
            supported_events = await self._get_camera_events_capabilities()

        events = []
        for event_id in EVENTS:
            if event_id in supported_events:
                event_info = EventInfo(
                    id=event_id,
                    device_info=channel_info,
                    unique_id=f"{slugify(self.serial_no.lower())}_{channel_id}_{event_id}",
                    url=get_event_url(event_id, channel_id, self.is_nvr),
                )
                events.append(event_info)

        return events

    async def _get_channel_events_by_requests(
        self, channel_info: DeviceInfo, channel_id: str
    ) -> list[EventInfo]:
        """Get available camera events by fetching particular event URL, requires more requests."""

        events = []
        for event_id in EVENTS:
            url = get_event_url(event_id, channel_id, self.is_nvr)
            try:
                response = await self.request(GET, url)
                _LOGGER.debug("%s/ISAPI/%s %s", self.isapi.host, url, response)
                event_info = EventInfo(
                    id=event_id,
                    device_info=channel_info,
                    unique_id=f"{slugify(self.serial_no.lower())}_{channel_id}_{event_id}",
                    url=url,
                )
                events.append(event_info)
            except (HTTPError, HTTPStatusError) as error:
                _LOGGER.debug("%s/ISAPI/%s %s", self.isapi.host, url, error)
        return events

    async def get_event_enabled_state(self, event: EventInfo) -> bool:
        """Get event detection state."""

        state = await self.request(GET, event.url)
        slug = EVENTS[event.id]["slug"]
        node = slug[0].upper() + slug[1:]
        return str_to_bool(state[node]["enabled"])

    async def set_event_enabled_state(self, event: EventInfo, is_enabled: bool) -> None:
        """Set event detection state."""

        data = await self.request(GET, event.url)
        _LOGGER.debug("%s/ISAPI/%s %s", self.isapi.host, event.url, data)
        slug = EVENTS[event.id]["slug"]
        node = slug[0].upper() + slug[1:]
        new_state = bool_to_str(is_enabled)
        if new_state == data[node]["enabled"]:
            return
        data[node]["enabled"] = new_state
        xml = xmltodict.unparse(data)
        response = await self.request(PUT, event.url, data=xml)
        _LOGGER.debug("[PUT] %s/ISAPI/%s %s", self.isapi.host, event.url, response)

    async def get_holiday_enabled_state(self, holiday_index=0) -> bool:
        """Get holiday state"""

        data = await self.isapi.System.Holidays(method=GET)
        holiday = data["HolidayList"]["holiday"][holiday_index]
        return str_to_bool(holiday["enabled"]["#text"])

    async def set_holiday_enabled_state(
        self, is_enabled: bool, holiday_index=0
    ) -> None:
        """Enable or disable holiday, by enable set time span to year starting from today."""

        data = await self.isapi.System.Holidays(method=GET)
        _LOGGER.debug("%s/ISAPI/System/Holidays %s", self.isapi.host, data)
        holiday = data["HolidayList"]["holiday"][holiday_index]
        new_state = bool_to_str(is_enabled)
        if new_state == holiday["enabled"]["#text"]:
            return
        holiday["enabled"]["#text"] = new_state
        if is_enabled:
            today = datetime.date.today()
            holiday["holidayMode"]["#text"] = "date"
            holiday["holidayDate"] = {
                "startDate": today.strftime("%Y-%m-%d"),
                "endDate": today.replace(year=today.year + 1).strftime("%Y-%m-%d"),
            }
            holiday.pop("holidayWeek", None)
            holiday.pop("holidayMonth", None)
        xml = xmltodict.unparse(data)
        response = await self.isapi.System.Holidays(method=PUT, data=xml)
        _LOGGER.debug("[PUT] %s/ISAPI/System/Holidays %s", self.isapi.host, response)

    def _get_event_notification_host(self, data: Node) -> Node:
        hosts = data["HttpHostNotificationList"]["HttpHostNotification"]
        if isinstance(hosts, list):
            # <HttpHostNotificationList xmlns="http://www.hikvision.com/ver20/XMLSchema">
            return hosts[0]
        # <HttpHostNotificationList xmlns="http://www.isapi.org/ver20/XMLSchema">
        return hosts

    async def get_alarm_server(self) -> Node:
        """Get event notifications listener server URL."""

        data = await self.isapi.Event.notification.httpHosts(method=GET)
        host = self._get_event_notification_host(data)
        return host

    async def set_alarm_server(self, base_url: str, path: str) -> None:
        """Set event notifications listener server."""

        address = urlparse(base_url)
        data = await self.isapi.Event.notification.httpHosts(method=GET)
        _LOGGER.debug("%s/ISAPI/Event/notification/httpHosts %s", self.isapi.host, data)
        host = self._get_event_notification_host(data)
        if (
            host["protocolType"] == address.scheme.upper()
            and host.get("ipAddress") == address.hostname
            and host.get("portNo") == str(address.port)
            and host["url"] == path
        ):
            return
        host["url"] = path
        host["protocolType"] = address.scheme.upper()
        host["parameterFormatType"] = "XML"
        host["addressingFormatType"] = "ipaddress"
        host["ipAddress"] = address.hostname
        host["portNo"] = address.port
        host["httpAuthenticationMethod"] = "none"

        xml = xmltodict.unparse(data)
        response = await self.isapi.Event.notification.httpHosts(method=PUT, data=xml)
        _LOGGER.debug(
            "[PUT] %s/ISAPI/Event/notification/httpHosts %s", self.isapi.host, response
        )

    async def request(self, method: str, url: str, **data) -> Any:
        """Send request"""

        full_url = f"{self.isapi.host}/{self.isapi.isapi_prefix}/{url}"
        return await self.isapi.common_request(
            method, full_url, "dict", self.isapi.timeout, **data
        )

    def handle_exception(self, ex: Exception, details: str = "") -> bool:
        """Common exception handler, returns False if exception remains unhandled"""

        host = self.isapi.host
        if isinstance(ex, HTTPStatusError):
            status_code = ex.response.status_code
            if status_code == HTTPStatus.UNAUTHORIZED:
                raise ConfigEntryAuthFailed(
                    f"Credentials expired for {host} {details}"
                ) from ex
        elif isinstance(ex, (asyncio.TimeoutError, TimeoutException)):
            raise ConfigEntryNotReady(
                f"Timeout while connecting to {host} {details}"
            ) from ex

        _LOGGER.warning("Unexpected exception %s %s", details, ex)
        return False

    @staticmethod
    def parse_event_notification(xml: str) -> AlertInfo:
        """Parse incoming EventNotificationAlert XML message."""

        data = xmltodict.parse(xml)
        alert = data["EventNotificationAlert"]

        channel_id = int(alert.get("channelID", alert.get("dynChannelID", "0")))
        if channel_id > 32:
            # workaround for wrong channelId provided by NVR
            # model: DS-7608NXI-I2/8P/S, Firmware: V4.61.067 or V4.62.200
            channel_id = channel_id - 32

        event_id = alert.get("eventType")
        if not event_id or event_id == "duration":
            # <EventNotificationAlert version="2.0"
            event_id = alert["DurationList"]["Duration"]["relationEvent"]
        event_id = event_id.lower()
        # handle alternate event type
        if EVENTS_ALTERNATE_ID.get(event_id):
            event_id = EVENTS_ALTERNATE_ID[event_id]

        device_serial = None
        if alert.get("Extensions"):
            # <EventNotificationAlert version="1.0"
            device_serial = alert["Extensions"]["serialNumber"]["#text"]

        # <EventNotificationAlert version="2.0"
        mac = alert.get("macAddress")

        if not EVENTS[event_id]:
            raise ValueError(f"Unsupported event {event_id}")

        return AlertInfo(channel_id, event_id, device_serial, mac)


def get_event_url(event_id: str, channel_id: str, is_proxy: bool) -> str:
    """Get event ISAPI URL."""

    event_type = EVENTS[event_id]["type"]
    slug = EVENTS[event_id]["slug"]
    if is_proxy and event_type == EVENT_BASIC:
        # ISAPI/ContentMgmt/InputProxy/channels/{channel_id}/video/{event}
        url = f"ContentMgmt/InputProxy/channels/{channel_id}/video/{slug}"
    elif not is_proxy and event_type == EVENT_BASIC:
        # ISAPI/System/Video/inputs/channels/{channel_id}/{event}
        url = f"System/Video/inputs/channels/{channel_id}/{slug}"
    else:
        # ISAPI/Smart/{event}/{channel_id}
        url = f"Smart/{slug}/{channel_id}"
    return url


def str_to_bool(value: str) -> bool:
    """Convert text to boolean."""
    return value.lower() == "true"


def bool_to_str(value: bool) -> str:
    """Convert boolean to 'true' or 'false'."""
    return "true" if value else "false"
