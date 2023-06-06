"""Hikvision ISAPI client"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import datetime
from http import HTTPStatus
import json
import logging
from typing import Any
from urllib.parse import urlparse

from hikvisionapi import AsyncClient
from httpx import HTTPStatusError, TimeoutException
import xmltodict

from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import slugify

from .const import (
    DEVICE_TYPE_ANALOG_CAMERA,
    DEVICE_TYPE_IP_CAMERA,
    DOMAIN,
    EVENT_BASIC,
    EVENTS,
    EVENTS_ALTERNATE_ID,
    MUTEX_ALTERNATE_IDS,
    STREAM_TYPE,
)

Node = dict[str, Any]

_LOGGER = logging.getLogger(__name__)

GET = "get"
PUT = "put"
POST = "post"


@dataclass
class AlertInfo:
    """Holds NVR/Camera event notification info"""

    channel_id: int
    event_id: str
    device_serial_no: str
    mac: str = ""


@dataclass
class MutexIssue:
    """Holds mutually exclusive event checking info"""

    event_id: str
    channels: list = field(default_factory=list)


@dataclass
class EventInfo:
    """Holds event info of particular device"""

    id: str
    unique_id: str
    url: str


@dataclass
class SupportedEventsInfo:
    """Holds event info for video channel"""

    channel_id: int
    event_id: str
    notifications: list[str] = field(default_factory=list)


@dataclass
class CameraStreamInfo:
    """Holds info of a camera stream"""

    id: int
    name: str
    type_id: int
    type: str
    enabled: bool
    codec: str
    width: int
    height: int
    audio: bool


@dataclass
class HDDInfo:
    id: int
    name: str
    type: str
    status: str
    capacity: int
    freespace: int
    property: str


@dataclass
class HIKDeviceInfo:
    """Holds info of an NVR/DVR or single IP Camera"""

    name: str
    manufacturer: str
    model: str
    serial_no: str
    firmware: str
    mac_address: str
    ip_address: str
    device_type: str
    is_nvr: bool = False
    support_analog_cameras: int = 0
    support_digital_cameras: int = 0
    support_holiday_mode: bool = False
    support_alarm_server: bool = False
    support_channel_zero: bool = False
    input_ports: int = 0
    output_ports: int = 0
    storage: list[HDDInfo] = field(default_factory=list)


@dataclass
class AnalogCamera:
    """Base class for IP(digital) and Analog cameras"""

    id: int
    name: str
    model: str
    serial_no: str
    input_port: int
    streams: list[CameraStreamInfo] = field(default_factory=list)
    supported_events: list[EventInfo] = field(default_factory=list)


@dataclass
class IPCamera(AnalogCamera):
    """IP/Digital camera info"""

    firmware: str = ""
    ip_addr: str = ""
    ip_port: int = 0


class ISAPI:
    """hikvisionapi async client wrapper."""

    def __init__(self, host: str, username: str, password: str) -> None:
        self.isapi = AsyncClient(host, username, password, timeout=20)
        self.host = host
        self.device_info = None
        self.supported_events: list[SupportedEventsInfo] = []
        self.cameras: list[IPCamera | AnalogCamera] = []

    async def get_hardware_info(self):
        # Get base hw info
        hw_info = (await self.isapi.System.deviceInfo(method=GET)).get("DeviceInfo", {})
        _LOGGER.debug("%s/ISAPI/System/deviceInfo %s", self.isapi.host, hw_info)

        # Get device capabilities
        capabilities = (await self.isapi.System.capabilities(method=GET)).get(
            "DeviceCap", {}
        )
        _LOGGER.debug("%s/ISAPI/System/capabilities %s", self.isapi.host, capabilities)

        # Set DeviceInfo
        self.device_info = HIKDeviceInfo(
            name=hw_info.get("deviceName"),
            manufacturer=str(hw_info.get("manufacturer", "Hikvision")).title(),
            model=hw_info.get("model"),
            serial_no=hw_info.get("serialNumber"),
            firmware=hw_info.get("firmwareVersion"),
            mac_address=hw_info.get("macAddress"),
            ip_address=urlparse(self.host).hostname,
            device_type=hw_info.get("deviceType"),
            support_analog_cameras=int(
                capabilities.get("SysCap", {})
                .get("VideoCap", {})
                .get("videoInputPortNums", 0)
            ),
            support_digital_cameras=int(
                capabilities.get("RacmCap", {}).get("inputProxyNums", 0)
            ),
            support_holiday_mode=capabilities.get("SysCap", {}).get(
                "isSupportHolidy", False
            ),
            support_alarm_server=True if await self.get_alarm_server() else False,
            support_channel_zero=capabilities.get("RacmCap", {}).get(
                "isSupportZeroChan", False
            ),
            input_ports=int(
                capabilities.get("SysCap", {})
                .get("IOCap", {})
                .get("IOInputPortNums", 0)
            ),
            output_ports=int(
                capabilities.get("SysCap", {})
                .get("IOCap", {})
                .get("IOOutputPortNums", 0)
            ),
            storage=await self.get_storage_devices(),
        )

        # Set if NVR based on whether more than 1 supported IP or analog cameras
        # Single IP camera will show 0 supported devices in total
        if (
            self.device_info.support_analog_cameras
            + self.device_info.support_digital_cameras
            > 0
        ):
            self.device_info.is_nvr = True

        await self.get_cameras()

    async def get_storage_devices(self):
        storage_list = []
        storage_info = (
            (await self.isapi.ContentMgmt.Storage(method=GET))
            .get("storage", {})
            .get("hddList", {})
        )

        if not isinstance(storage_info, list):
            storage_info = [storage_info]

        _LOGGER.debug("%s/ISAPI/ContentMgmt/Storage %s", self.isapi.host, storage_info)

        for storage in storage_info:
            storage = storage.get("hdd")
            if storage:
                storage_list.append(
                    HDDInfo(
                        id=int(storage.get("id")),
                        name=storage.get("hddName"),
                        type=storage.get("hddType"),
                        status=storage.get("status"),
                        capacity=int(storage.get("capacity")),
                        freespace=int(storage.get("freeSpace")),
                        property=storage.get("property"),
                    )
                )

        return storage_list

    async def get_cameras(self):
        # Get all supported events to reduce isapi queries
        supported_events = await self.get_supported_events_info()

        # Get digital cameras
        if not self.device_info.is_nvr:
            self.cameras.append(
                IPCamera(
                    id=1,
                    name=self.device_info.name,
                    model=self.device_info.model,
                    serial_no=self.device_info.serial_no,
                    firmware=self.device_info.firmware,
                    input_port=1,
                    ip_addr=self.device_info.ip_address,
                    streams=await self.get_camera_streams(1),
                    supported_events=await self.get_camera_event_capabilities(
                        supported_events, 1, DEVICE_TYPE_IP_CAMERA
                    ),
                )
            )
        else:
            if self.device_info.support_digital_cameras > 0:
                digital_cameras = (
                    (await self.isapi.ContentMgmt.InputProxy.channels(method=GET))
                    .get("InputProxyChannelList", {})
                    .get("InputProxyChannel", [])
                )

                if not isinstance(digital_cameras, list):
                    digital_cameras = [digital_cameras]

                _LOGGER.debug(
                    "%s/ISAPI/ContentMgmt/InputProxy/channels %s",
                    self.isapi.host,
                    digital_cameras,
                )

                for digital_camera in digital_cameras:
                    camera_id = digital_camera.get("id")
                    self.cameras.append(
                        IPCamera(
                            id=camera_id,
                            name=digital_camera.get("name"),
                            model=digital_camera.get(
                                "sourceInputPortDescriptor", {}
                            ).get("model"),
                            serial_no=digital_camera.get(
                                "sourceInputPortDescriptor", {}
                            ).get("serialNumber"),
                            firmware=digital_camera.get(
                                "sourceInputPortDescriptor", {}
                            ).get("firmwareVersion"),
                            input_port=digital_camera.get(
                                "sourceInputPortDescriptor", {}
                            ).get("srcInputPort"),
                            ip_addr=digital_camera.get(
                                "sourceInputPortDescriptor", {}
                            ).get("ipAddress"),
                            ip_port=digital_camera.get(
                                "sourceInputPortDescriptor", {}
                            ).get("managePortNo"),
                            streams=await self.get_camera_streams(camera_id),
                            supported_events=await self.get_camera_event_capabilities(
                                supported_events, camera_id, DEVICE_TYPE_IP_CAMERA
                            ),
                        )
                    )

            # Get analog cameras
            if self.device_info.support_analog_cameras > 0:
                analog_cameras = (
                    (await self.isapi.System.Video.inputs.channels(method=GET))
                    .get("VideoInputChannelList", {})
                    .get("VideoInputChannel", [])
                )

                if not isinstance(analog_cameras, list):
                    analog_cameras = [analog_cameras]

                _LOGGER.debug(
                    "%s/ISAPI/System/Video/inputs %s", self.isapi.host, analog_cameras
                )

                for analog_camera in analog_cameras:
                    camera_id = analog_camera.get("id")
                    device_serial_no = f"{self.device_info.serial_no}-VI{camera_id}"

                    self.cameras.append(
                        AnalogCamera(
                            id=int(camera_id),
                            name=analog_camera.get("name"),
                            model=analog_camera.get("resDesc"),
                            serial_no=device_serial_no,
                            input_port=analog_camera.get("inputPort"),
                            streams=await self.get_camera_streams(camera_id),
                            supported_events=await self.get_camera_event_capabilities(
                                supported_events, camera_id, DEVICE_TYPE_ANALOG_CAMERA
                            ),
                        )
                    )

        _LOGGER.debug(self.cameras)

    async def get_camera_event_capabilities(
        self,
        supported_events: list[SupportedEventsInfo],
        channel_id: int,
        camera_type: str,
    ) -> list[EventInfo]:
        events = []

        camera_supported_events = [
            s for s in supported_events if s.channel_id == int(channel_id)
        ]

        for event in camera_supported_events:
            if EVENTS.get(event.event_id):
                event_info = EventInfo(
                    id=event.event_id,
                    unique_id=f"{slugify(self.device_info.serial_no.lower())}_{channel_id}_{event.event_id}",
                    url=self.get_event_url(
                        event.event_id, channel_id, self.device_info.is_nvr, camera_type
                    ),
                )
                events.append(event_info)
        return events

    async def get_supported_events_info(self):
        events = []
        if self.device_info.is_nvr:
            supported_events = (
                (await self.isapi.Event.triggers(method=GET))
                .get("EventTriggerList", {})
                .get("EventTrigger")
            )
        else:
            supported_events = (
                (await self.isapi.Event.triggers(method=GET))
                .get("EventNotification", {})
                .get("EventTriggerList", {})
                .get("EventTrigger")
            )

        _LOGGER.debug("%s/ISAPI/Event/triggers %s", self.isapi.host, supported_events)

        for support_event in supported_events:
            event_type = support_event.get("eventType")
            channel = support_event.get("videoInputChannelID", "0")
            notifications = support_event.get("EventTriggerNotificationList", {})

            # Fix for empty EventTriggerNotificationList in IP camera
            if not notifications:
                continue

            notifications = notifications.get("EventTriggerNotification", [])

            if not isinstance(notifications, list):
                notifications = [notifications]

            # Translate to alternate IDs
            if event_type.lower() in EVENTS_ALTERNATE_ID.keys():
                event_type = EVENTS_ALTERNATE_ID[event_type.lower()]

            events.append(
                SupportedEventsInfo(
                    channel_id=int(channel),
                    event_id=event_type.lower(),
                    notifications=[
                        notify.get("notificationMethod") for notify in notifications
                    ]
                    if notifications
                    else [],
                )
            )

        return events

    def get_event_url(
        self, event_id: str, channel_id: int, is_nvr: bool, camera_type: str
    ) -> str:
        """Get event ISAPI URL."""

        event_type = EVENTS[event_id]["type"]
        slug = EVENTS[event_id]["slug"]

        if (
            is_nvr
            and camera_type == DEVICE_TYPE_IP_CAMERA
            and event_type == EVENT_BASIC
        ):
            # ISAPI/ContentMgmt/InputProxy/channels/{channel_id}/video/{event}
            url = f"ContentMgmt/InputProxy/channels/{channel_id}/video/{slug}"
        elif (
            is_nvr and camera_type == DEVICE_TYPE_ANALOG_CAMERA or not is_nvr
        ) and event_type == EVENT_BASIC:
            # ISAPI/System/Video/inputs/channels/{channel_id}/{event}
            url = f"System/Video/inputs/channels/{channel_id}/{slug}"
        else:
            # ISAPI/Smart/{event}/{channel_id}
            url = f"Smart/{slug}/{channel_id}"
        return url

    async def get_camera_streams(self, channel_id: int) -> list[CameraStreamInfo]:
        # TODO - Improve efficiency of this by reading once and adding to each camera
        streams = []
        for id, stream_type in STREAM_TYPE.items():
            try:
                stream_id = f"{channel_id}0{id}"
                stream_info = (
                    await self.isapi.Streaming.channels[stream_id](method=GET)
                ).get("StreamingChannel")
                _LOGGER.debug(
                    "%s/ISAPI/Streaming/channels/%s %s",
                    self.isapi.host,
                    stream_id,
                    stream_info,
                )
                streams.append(
                    CameraStreamInfo(
                        id=stream_info["id"],
                        name=stream_info["channelName"],
                        type_id=id,
                        type=stream_type,
                        enabled=stream_info["enabled"],
                        codec=stream_info["Video"]["videoCodecType"],
                        width=stream_info["Video"]["videoResolutionWidth"],
                        height=stream_info["Video"]["videoResolutionHeight"],
                        audio=stream_info.get("Audio", {}).get("enabled", False),
                    )
                )
            except HTTPStatusError as ex:
                # If http 400 then does not support this stream type
                continue
        return streams

    def get_camera_by_id(self, id: int) -> IPCamera | AnalogCamera | None:
        try:
            return [camera for camera in self.cameras if camera.id == id][0]
        except IndexError:
            # Camera id does not exist
            return None

    def get_device_info(self, device_id: int = 0) -> DeviceInfo:
        """Return device registry information."""
        if device_id == 0:
            return DeviceInfo(
                manufacturer=self.device_info.manufacturer,
                identifiers={(DOMAIN, self.device_info.serial_no)},
                connections={(dr.CONNECTION_NETWORK_MAC, self.device_info.mac_address)},
                model=self.device_info.model,
                name=self.device_info.name,
                sw_version=self.device_info.firmware,
            )
        else:
            camera_info = self.get_camera_by_id(device_id)
            is_ip_camera = True if isinstance(camera_info, IPCamera) else False

            return DeviceInfo(
                manufacturer=self.device_info.manufacturer,
                identifiers={(DOMAIN, camera_info.serial_no)},
                model=camera_info.model,
                name=camera_info.name,
                sw_version=self.device_info.firmware if is_ip_camera else "Unknown",
                via_device=(DOMAIN, self.device_info.serial_no)
                if self.device_info.is_nvr
                else None,
            )

    async def get_event_enabled_state(self, event: EventInfo) -> bool:
        """Get event detection state."""

        state = await self.request(GET, event.url)
        slug = EVENTS[event.id]["slug"]
        node = slug[0].upper() + slug[1:]
        return str_to_bool(state[node]["enabled"])

    async def get_event_switch_mutex(
        self, event: EventInfo, channel_id: int
    ) -> list[MutexIssue]:
        mutex_issues = []

        if not EVENTS[event.id].get("mutex"):
            return mutex_issues

        # Use alt event ID for mutex due to crap API!
        event_id = event.id
        if MUTEX_ALTERNATE_IDS.get(event.id):
            event_id = MUTEX_ALTERNATE_IDS[event.id]

        data = {"function": event_id, "channelID": int(channel_id)}
        url = "System/mutexFunction?format=json"
        try:
            response = await self.request(
                POST, url, present="json", data=json.dumps(data)
            )
        except HTTPStatusError as ex:
            # TODO: validate this if getting a 403 error!
            return True

        response = json.loads(response)

        if mutex_list := response.get("MutexFunctionList"):
            for mutex_item in mutex_list:
                mutex_event_id = mutex_item.get("mutexFunction")
                if EVENTS_ALTERNATE_ID.get(mutex_event_id):
                    mutex_event_id = EVENTS_ALTERNATE_ID[mutex_event_id]

                mutex_issues.append(
                    MutexIssue(
                        event_id=mutex_event_id, channels=mutex_item.get("channelID")
                    )
                )
        return mutex_issues

    async def set_event_enabled_state(
        self, channel_id: int, event: EventInfo, is_enabled: bool
    ) -> None:
        """Set event detection state."""

        # Validate that this event switch is not mutually exclusive with another enabled one
        if not (mutex_issues := await self.get_event_switch_mutex(event, channel_id)):
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
        else:
            raise HomeAssistantError(
                f"You cannot enable {EVENTS[event.id]['label']} events.  Please disable {EVENTS[mutex_issues[0].event_id]['label']} on channels {mutex_issues[0].channels} first"
            )

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

    async def request(
        self, method: str, url: str, present: str = "dict", **data
    ) -> Any:
        """Send request"""

        full_url = f"{self.isapi.host}/{self.isapi.isapi_prefix}/{url}"
        try:
            return await self.isapi.common_request(
                method, full_url, present, self.isapi.timeout, **data
            )
        except HTTPStatusError as ex:
            raise HomeAssistantError(
                f"Unable to perform requested action. Error is {ex}"
            )

    def handle_exception(self, ex: Exception, details: str = "") -> bool:
        """Common exception handler, returns False if exception remains unhandled"""

        def is_reauth_needed():
            if isinstance(ex, HTTPStatusError):
                status_code = ex.response.status_code
                if status_code in (
                    HTTPStatus.UNAUTHORIZED,
                    HTTPStatus.FORBIDDEN,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                ):
                    return True
            return False

        host = self.isapi.host
        if is_reauth_needed():
            raise ConfigEntryAuthFailed(
                f"Credentials expired for {host} {details}"
            ) from ex

        elif isinstance(ex, (asyncio.TimeoutError, TimeoutException)):
            raise ConfigEntryNotReady(
                f"Timeout while connecting to {host} {details}"
            ) from ex

        _LOGGER.warning("Unexpected exception | %s | %s", details, ex)
        return False

    async def get_camera_image(
        self,
        stream: CameraStreamInfo,
        width: int | None = None,
        height: int | None = None,
    ):
        """Get camera snapshot."""
        params = {}
        if not width or width > 100:
            params = {
                "videoResolutionWidth": stream.width,
                "videoResolutionHeight": stream.height,
            }
        chunks = self.isapi.Streaming.channels[stream.id].picture(
            method=GET, type="opaque_data", params=params
        )
        image_bytes = b"".join([chunk async for chunk in chunks])
        return image_bytes

    def get_stream_source(self, stream: CameraStreamInfo) -> str:
        """Get stream source."""
        return f"rtsp://{self.isapi.login}:{self.isapi.password}@{self.device_info.ip_address}/Streaming/channels/{stream.id}"


def str_to_bool(value: str) -> bool:
    """Convert text to boolean."""
    return value.lower() == "true"


def bool_to_str(value: bool) -> str:
    """Convert boolean to 'true' or 'false'."""
    return "true" if value else "false"


def get_stream_id(channel_id: str, stream_type: int = 1) -> int:
    """Get stream id."""
    return int(channel_id) * 100 + stream_type
