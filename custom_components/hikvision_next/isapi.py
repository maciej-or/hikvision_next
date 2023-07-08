"""Hikvision ISAPI client"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import datetime
from functools import reduce
from http import HTTPStatus
import json
import logging
from typing import Any, Optional
from urllib.parse import urlparse

from hikvisionapi import AsyncClient
from httpx import HTTPStatusError, TimeoutException
import xmltodict

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import slugify

from .const import (
    CONNECTION_TYPE_DIRECT,
    CONNECTION_TYPE_PROXIED,
    DOMAIN,
    EVENT_BASIC,
    EVENT_IO,
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
class AlarmServer:
    """Holds alarm server info"""

    # Uses pylint invalid names to not break previous versions
    ipAddress: str  # pylint: disable=invalid-name
    portNo: int  # pylint: disable=invalid-name
    url: str  # pylint: disable=invalid-name
    protocolType: str  # pylint: disable=invalid-name


@dataclass
class AlertInfo:
    """Holds NVR/Camera event notification info"""

    channel_id: int
    io_port_id: int
    event_id: str
    device_serial_no: Optional[str]
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
    channel_id: int
    io_port_id: int
    unique_id: str
    url: str
    notifiers: list[str] = field(default_factory=list)


@dataclass
class SupportedEventsInfo:
    """Holds supported event info for NVR/IP Camera"""

    channel_id: int
    io_port_id: int
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
    """Holds info for internal storage devices"""

    id: int
    name: str
    type: str
    status: str
    capacity: int
    freespace: int
    property: str


@dataclass
class HikDeviceInfo:
    """Holds info of an NVR/DVR or single IP Camera"""

    name: str = ""
    manufacturer: str = ""
    model: str = ""
    serial_no: str = ""
    firmware: str = ""
    mac_address: str = ""
    ip_address: str = ""
    device_type: str = ""
    is_nvr: bool = False
    support_analog_cameras: int = 0
    support_digital_cameras: int = 0
    support_holiday_mode: bool = False
    support_alarm_server: bool = False
    support_channel_zero: bool = False
    support_event_mutex_checking: bool = False
    input_ports: int = 0
    output_ports: int = 0
    rtsp_port: int = 554
    storage: list[HDDInfo] = field(default_factory=list)
    supported_events: list[EventInfo] = field(default_factory=list)


@dataclass
class AnalogCamera:
    """Analog cameras info"""

    id: int
    name: str
    model: str
    serial_no: str
    input_port: int
    connection_type: str
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
        self.device_info = HikDeviceInfo()
        self.cameras: list[IPCamera | AnalogCamera] = []
        self.supported_events: list[SupportedEventsInfo] = []

    async def get_hardware_info(self):
        """Get base device data."""
        # Get base hw info
        hw_info = (await self.isapi.System.deviceInfo(method=GET)).get("DeviceInfo", {})
        _LOGGER.debug("%s/ISAPI/System/deviceInfo %s", self.isapi.host, hw_info)

        # Get device capabilities
        capabilities = (await self.isapi.System.capabilities(method=GET)).get("DeviceCap", {})
        _LOGGER.debug("%s/ISAPI/System/capabilities %s", self.isapi.host, capabilities)

        # Get all supported events to reduce isapi queries
        self.supported_events = await self.get_supported_events_info()

        # Set DeviceInfo
        self.device_info = HikDeviceInfo(
            name=hw_info.get("deviceName"),
            manufacturer=str(hw_info.get("manufacturer", "Hikvision")).title(),
            model=hw_info.get("model"),
            serial_no=hw_info.get("serialNumber"),
            firmware=hw_info.get("firmwareVersion"),
            mac_address=hw_info.get("macAddress"),
            ip_address=urlparse(self.host).hostname,  # type: ignore
            device_type=hw_info.get("deviceType"),
            support_analog_cameras=int(deep_get(capabilities, "SysCap.VideoCap.videoInputPortNums", 0)),
            support_digital_cameras=int(deep_get(capabilities, "RacmCap.inputProxyNums", 0)),
            support_holiday_mode=deep_get(capabilities, "SysCap.isSupportHolidy", False),
            support_alarm_server=bool(await self.get_alarm_server()),
            support_channel_zero=deep_get(capabilities, "RacmCap.isSupportZeroChan", False),
            support_event_mutex_checking=capabilities.get("isSupportGetmutexFuncErrMsg", False),
            input_ports=int(deep_get(capabilities, "SysCap.IOCap.IOInputPortNums", 0)),
            output_ports=int(deep_get(capabilities, "SysCap.IOCap.IOOutputPortNums", 0)),
            storage=await self.get_storage_devices(),
            supported_events=await self.get_device_event_capabilities(
                self.supported_events, hw_info.get("serialNumber"), 0
            )
        )

        await self.get_protocols()

        # Set if NVR based on whether more than 1 supported IP or analog cameras
        # Single IP camera will show 0 supported devices in total
        if self.device_info.support_analog_cameras + self.device_info.support_digital_cameras > 1:
            self.device_info.is_nvr = True

    async def get_cameras(self):
        """Get camera objects for all connected cameras."""

        if not self.device_info.is_nvr:
            # Get single IP camera
            self.cameras.append(
                IPCamera(
                    id=1,
                    name=self.device_info.name,
                    model=self.device_info.model,
                    serial_no=self.device_info.serial_no,
                    firmware=self.device_info.firmware,
                    input_port=1,
                    connection_type=CONNECTION_TYPE_DIRECT,
                    ip_addr=self.device_info.ip_address,
                    streams=await self.get_camera_streams(1),
                    supported_events=await self.get_device_event_capabilities(
                        self.supported_events, self.device_info.serial_no, 1, CONNECTION_TYPE_DIRECT
                    ),
                )
            )
        else:
            # Get analog and digital cameras attached to NVR
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
                    source = digital_camera.get("sourceInputPortDescriptor")
                    if not source:
                        continue

                    # Generate serial number if not provided by camera
                    # As combination of protocol and IP
                    serial_no = source.get("serialNumber")

                    if not serial_no:
                        serial_no = str(source.get("proxyProtocol")) + str(source.get("ipAddress", "")).replace(".", "")

                    self.cameras.append(
                        IPCamera(
                            id=int(camera_id),
                            name=digital_camera.get("name"),
                            model=source.get("model", "Unknown"),
                            serial_no=serial_no,
                            firmware=source.get("firmwareVersion"),
                            input_port=int(source.get("srcInputPort")),
                            connection_type=CONNECTION_TYPE_PROXIED,
                            ip_addr=source.get("ipAddress"),
                            ip_port=source.get("managePortNo"),
                            streams=await self.get_camera_streams(camera_id),
                            supported_events=await self.get_device_event_capabilities(
                                self.supported_events,
                                self.device_info.serial_no,
                                camera_id,
                                CONNECTION_TYPE_PROXIED,
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
                    "%s/ISAPI/System/Video/inputs %s",
                    self.isapi.host,
                    analog_cameras,
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
                            input_port=int(analog_camera.get("inputPort")),
                            connection_type=CONNECTION_TYPE_DIRECT,
                            streams=await self.get_camera_streams(camera_id),
                            supported_events=await self.get_device_event_capabilities(
                                self.supported_events,
                                self.device_info.serial_no,
                                camera_id,
                                CONNECTION_TYPE_DIRECT,
                            ),
                        )
                    )

        _LOGGER.debug("Cameras: %s", self.cameras)

    async def get_protocols(self):
        """Get protocols and ports"""
        try:
            protocols = deep_get(
                await self.isapi.Security.adminAccesses(method=GET),
                "AdminAccessProtocolList.AdminAccessProtocol",
                [],
            )
            _LOGGER.debug(
                "%s/ISAPI/Security/adminAccesses %s",
                self.isapi.host,
                protocols,
            )

            for item in protocols:
                if item.get("protocol") == "RTSP" and item.get("portNo"):
                    self.device_info.rtsp_port = item.get("portNo")
                    break

        except HTTPStatusError:
            pass

    async def get_device_event_capabilities(
        self,
        supported_events: list[SupportedEventsInfo],
        serial_no: str,
        device_id: int,
        connection_type: str = CONNECTION_TYPE_DIRECT,
    ) -> list[EventInfo]:
        """Get events support by device (device id  NVR = 0, camera > 0) and integration"""
        events = []

        if device_id == 0:  # NVR
            device_supported_events = [s for s in supported_events if (
                s.event_id in EVENTS and EVENTS[s.event_id].get("type") == EVENT_IO
            )]
        else:  # Camera
            device_supported_events = [s for s in supported_events if (
                s.channel_id == int(device_id)
                and s.event_id in EVENTS
            )]

        for event in device_supported_events:
            # Build unique_id
            device_id_param = f"_{device_id}" if device_id != 0 else ""
            io_port_id_param = f"_{event.io_port_id}" if event.io_port_id != 0 else ""
            unique_id = (
                f"{slugify(serial_no.lower())}{device_id_param}{io_port_id_param}_{event.event_id}"
            )

            if EVENTS.get(event.event_id):
                event_info = EventInfo(
                    id=event.event_id,
                    channel_id=event.channel_id,
                    io_port_id=event.io_port_id,
                    unique_id=unique_id,
                    url=self.get_event_url(event, connection_type),
                    notifiers=event.notifications,
                )
                events.append(event_info)
        return events

    async def get_supported_events_info(self):
        """Get list of all supported events available"""
        events = []
        event_triggers = await self.isapi.Event.triggers(method=GET)
        event_notification = event_triggers.get("EventNotification")
        _LOGGER.debug("%s/ISAPI/Event/triggers %s", self.isapi.host, event_triggers)
        if event_notification:
            supported_events = deep_get(event_notification, "EventTriggerList.EventTrigger")
        else:
            supported_events = deep_get(event_triggers, "EventTriggerList.EventTrigger")

        for support_event in supported_events:
            notifications = support_event.get("EventTriggerNotificationList", {})
            # Fix for empty EventTriggerNotificationList in IP camera
            if not notifications:
                continue

            event_type = support_event.get("eventType")
            channel = support_event.get("videoInputChannelID", support_event.get("dynVideoInputChannelID", 0))
            io_port = support_event.get("inputIOPortID", support_event.get("dynInputIOPortID", 0))
            notifications = notifications.get("EventTriggerNotification", [])

            if not isinstance(notifications, list):
                notifications = [notifications]

            # Translate to alternate IDs
            if event_type.lower() in EVENTS_ALTERNATE_ID:
                event_type = EVENTS_ALTERNATE_ID[event_type.lower()]

            events.append(
                SupportedEventsInfo(
                    channel_id=int(channel),
                    io_port_id=int(io_port),
                    event_id=event_type.lower(),
                    notifications=[notify.get("notificationMethod") for notify in notifications]
                    if notifications
                    else [],
                )
            )

        return events

    def get_event_url(self, event: SupportedEventsInfo, connection_type: str) -> str:
        """Get event ISAPI URL."""

        event_type = EVENTS[event.event_id]["type"]
        slug = EVENTS[event.event_id]["slug"]

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
        else:
            # ISAPI/Smart/{event}/{channel_id}
            url = f"Smart/{slug}/{event.channel_id}"
        return url

    async def get_camera_streams(self, channel_id: int) -> list[CameraStreamInfo]:
        """Get stream info for all cameras"""
        streams = []
        for stream_type_id, stream_type in STREAM_TYPE.items():
            try:
                stream_id = f"{channel_id}0{stream_type_id}"
                stream_info = (await self.isapi.Streaming.channels[stream_id](method=GET)).get("StreamingChannel")
                _LOGGER.debug(
                    "%s/ISAPI/Streaming/channels/%s %s",
                    self.isapi.host,
                    stream_id,
                    stream_info,
                )
                streams.append(
                    CameraStreamInfo(
                        id=int(stream_info["id"]),
                        name=stream_info["channelName"],
                        type_id=stream_type_id,
                        type=stream_type,
                        enabled=stream_info["enabled"],
                        codec=stream_info["Video"]["videoCodecType"],
                        width=stream_info["Video"]["videoResolutionWidth"],
                        height=stream_info["Video"]["videoResolutionHeight"],
                        audio=deep_get(stream_info, "Audio.enabled", False),
                    )
                )
            except HTTPStatusError:
                # If http 400 then does not support this stream type
                continue
        return streams

    def get_camera_by_id(self, camera_id: int) -> IPCamera | AnalogCamera | None:
        """Get camera object by id."""
        try:
            return [camera for camera in self.cameras if camera.id == camera_id][0]
        except IndexError:
            # Camera id does not exist
            return None

    async def get_storage_devices(self):
        """Get HDD storage devices."""
        storage_list = []
        storage_info = (await self.isapi.ContentMgmt.Storage(method=GET)).get("storage", {}).get("hddList", {})

        if not isinstance(storage_info, list):
            storage_info = [storage_info]

        _LOGGER.debug("%s/ISAPI/ContentMgmt/Storage %s", self.isapi.host, storage_info)

        for storage in storage_info:
            if storage := storage.get("hdd"):
                if not isinstance(storage, list):
                    storage = [storage]
                if storage:
                    for hdd in storage:
                        storage_list.append(
                            HDDInfo(
                                id=int(hdd.get("id")),
                                name=hdd.get("hddName"),
                                type=hdd.get("hddType"),
                                status=hdd.get("status"),
                                capacity=int(hdd.get("capacity")),
                                freespace=int(hdd.get("freeSpace")),
                                property=hdd.get("property"),
                            )
                        )

        return storage_list

    def get_storage_device_by_id(self, device_id: int) -> HDDInfo | None:
        """Get storage object by id."""
        try:
            return [storage_device for storage_device in self.device_info.storage if storage_device.id == device_id][0]
        except IndexError:
            # Storage id does not exist
            return None

    async def get_port_status(self, port_type: str, port_no: int) -> str:
        """Get status of physical ports"""
        if port_type == "input":
            status = await self.isapi.System.IO.inputs[port_no].status(method=GET)
            _LOGGER.debug("%s/ISAPI/System/IO/inputs/%s/status %s", self.isapi.host, port_no, status)
        else:
            status = await self.isapi.System.IO.outputs[port_no].status(method=GET)
            _LOGGER.debug("%s/ISAPI/System/IO/outputs/%s/status %s", self.isapi.host, port_no, status)

        if status.get("IOPortStatus"):
            return status["IOPortStatus"].get("ioState")

    async def set_port_state(self, port_no: int, turn_on: bool):
        """Set status of output port"""
        data = {}
        if turn_on:
            data["IOPortData"] = {"outputState": "high"}
        else:
            data["IOPortData"] = {"outputState": "low"}

        xml = xmltodict.unparse(data)
        response = await self.isapi.System.IO.outputs[port_no].trigger(method=PUT, data=xml)
        _LOGGER.debug("[PUT] %s/ISAPI/System/IO/outputs/%s/trigger %s", self.isapi.host, port_no, response)

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
            is_ip_camera = isinstance(camera_info, IPCamera)

            return DeviceInfo(
                manufacturer=self.device_info.manufacturer,
                identifiers={(DOMAIN, camera_info.serial_no)},
                model=camera_info.model,
                name=camera_info.name,
                sw_version=camera_info.firmware if is_ip_camera else "Unknown",
                via_device=(DOMAIN, self.device_info.serial_no) if self.device_info.is_nvr else None,
            )
        
    def get_event_state_node(self, event: EventInfo) -> str:
        """Get xml key for event state"""
        slug = EVENTS[event.id]["slug"]

        # Alternate node name for some event types
        if event.channel_id == 0:  # NVR
            if EVENTS[event.id].get("direct_node"):
                slug = EVENTS[event.id]["direct_node"]
        else:
            camera = self.get_camera_by_id(event.channel_id)
            if camera.connection_type == CONNECTION_TYPE_DIRECT and EVENTS[event.id].get("direct_node"):
                slug = EVENTS[event.id]["direct_node"]

            if camera.connection_type == CONNECTION_TYPE_PROXIED and EVENTS[event.id].get("proxied_node"):
                slug = EVENTS[event.id]["proxied_node"]

        node = slug[0].upper() + slug[1:]
        return node

    async def get_event_enabled_state(self, event: EventInfo) -> bool:
        """Get event detection state."""
        state = await self.request(GET, event.url)
        node = self.get_event_state_node(event)
        return str_to_bool(state[node].get("enabled", False)) if state.get(node) else False

    async def get_event_switch_mutex(self, event: EventInfo, channel_id: int) -> list[MutexIssue]:
        """Get if event is mutually exclusive with enabled events"""
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
            response = await self.request(POST, url, present="json", data=json.dumps(data))
        except HTTPStatusError:
            return []

        response = json.loads(response)

        if mutex_list := response.get("MutexFunctionList"):
            for mutex_item in mutex_list:
                mutex_event_id = mutex_item.get("mutexFunction")
                if EVENTS_ALTERNATE_ID.get(mutex_event_id):
                    mutex_event_id = EVENTS_ALTERNATE_ID[mutex_event_id]

                mutex_issues.append(
                    MutexIssue(
                        event_id=mutex_event_id,
                        channels=mutex_item.get("channelID"),
                    )
                )
        return mutex_issues

    async def set_event_enabled_state(self, channel_id: int, event: EventInfo, is_enabled: bool) -> None:
        """Set event detection state."""

        # Validate that this event switch is not mutually exclusive with another enabled one
        mutex_issues = []
        if channel_id != 0 and is_enabled and self.device_info.support_event_mutex_checking:
            mutex_issues = await self.get_event_switch_mutex(event, channel_id)

        if not mutex_issues:
            data = await self.request(GET, event.url)
            _LOGGER.debug("%s/ISAPI/%s %s", self.isapi.host, event.url, data)
            node = self.get_event_state_node(event)
            new_state = bool_to_str(is_enabled)
            if new_state == data[node]["enabled"]:
                return
            data[node]["enabled"] = new_state
            xml = xmltodict.unparse(data)
            response = await self.request(PUT, event.url, data=xml)
            _LOGGER.debug("[PUT] %s/ISAPI/%s %s", self.isapi.host, event.url, response)
        else:
            raise HomeAssistantError(
                f"You cannot enable {EVENTS[event.id]['label']} events. Please disable {EVENTS[mutex_issues[0].event_id]['label']} on channels {mutex_issues[0].channels} first"
            )

    async def get_holiday_enabled_state(self, holiday_index=0) -> bool:
        """Get holiday state"""

        data = await self.isapi.System.Holidays(method=GET)
        holiday = data["HolidayList"]["holiday"][holiday_index]
        return str_to_bool(holiday["enabled"]["#text"])

    async def set_holiday_enabled_state(self, is_enabled: bool, holiday_index=0) -> None:
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
        hosts = deep_get(data, "HttpHostNotificationList.HttpHostNotification", {})
        if isinstance(hosts, list):
            # <HttpHostNotificationList xmlns="http://www.hikvision.com/ver20/XMLSchema">
            return hosts[0]
        # <HttpHostNotificationList xmlns="http://www.isapi.org/ver20/XMLSchema">
        return hosts

    async def get_alarm_server(self) -> AlarmServer | None:
        """Get event notifications listener server URL."""

        try:
            data = await self.isapi.Event.notification.httpHosts(method=GET)
        except HTTPStatusError:
            return None

        _LOGGER.debug("%s/ISAPI/Event/notification/httpHosts %s", self.isapi.host, data)

        host = self._get_event_notification_host(data)

        alarm_server = AlarmServer(
            ipAddress=host.get("ipAddress"),
            portNo=int(host.get("portNo")),
            url=host.get("url"),
            protocolType=host.get("protocolType"),
        )
        _LOGGER.debug("Alarm Server: %s", alarm_server)
        return alarm_server

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
            "[PUT] %s/ISAPI/Event/notification/httpHosts %s",
            self.isapi.host,
            response,
        )

    async def request(self, method: str, url: str, present: str = "dict", **data) -> Any:
        """Send request"""

        full_url = f"{self.isapi.host}/{self.isapi.isapi_prefix}/{url}"
        try:
            return await self.isapi.common_request(method, full_url, present, self.isapi.timeout, **data)
        except HTTPStatusError as ex:
            raise ex

    def handle_exception(self, ex: Exception, details: str = "") -> bool:
        """Common exception handler, returns False if exception remains unhandled"""

        def is_reauth_needed():
            if isinstance(ex, HTTPStatusError):
                status_code = ex.response.status_code
                if status_code in (HTTPStatus.UNAUTHORIZED,):
                    return True
            return False

        host = self.isapi.host
        if is_reauth_needed():
            # Re-establish session
            self.isapi = AsyncClient(
                self.isapi.host,
                self.isapi.login,
                self.isapi.password,
                timeout=20,
            )
            return True

        elif isinstance(ex, (asyncio.TimeoutError, TimeoutException)):
            raise HomeAssistantError(f"Timeout while connecting to {host} {details}") from ex

        _LOGGER.warning("Unexpected exception | %s | %s", details, ex)
        return False

    @staticmethod
    def parse_event_notification(xml: str) -> AlertInfo:
        """Parse incoming EventNotificationAlert XML message."""

        # Fix for some cameras sending non html encoded data
        xml = xml.replace("&", "&amp;")

        data = xmltodict.parse(xml)
        alert = data["EventNotificationAlert"]

        event_id = alert.get("eventType")
        if not event_id or event_id == "duration":
            # <EventNotificationAlert version="2.0"
            event_id = alert["DurationList"]["Duration"]["relationEvent"]
        event_id = event_id.lower()

        # handle alternate event type
        if EVENTS_ALTERNATE_ID.get(event_id):
            event_id = EVENTS_ALTERNATE_ID[event_id]

        channel_id = int(alert.get("channelID", alert.get("dynChannelID", 0)))
        io_port_id = int(alert.get("inputIOPortID", 0))
        # <EventNotificationAlert version="1.0"
        device_serial = deep_get(alert, "Extensions.serialNumber.#text")
        # <EventNotificationAlert version="2.0"
        mac = alert.get("macAddress")

        if not EVENTS[event_id]:
            raise ValueError(f"Unsupported event {event_id}")

        return AlertInfo(channel_id, io_port_id, event_id, device_serial, mac)

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
        chunks = self.isapi.Streaming.channels[stream.id].picture(method=GET, type="opaque_data", params=params)
        image_bytes = b"".join([chunk async for chunk in chunks])
        return image_bytes

    def get_stream_source(self, stream: CameraStreamInfo) -> str:
        """Get stream source."""
        return f"rtsp://{self.isapi.login}:{self.isapi.password}@{self.device_info.ip_address}:{self.device_info.rtsp_port}/Streaming/channels/{stream.id}"


def str_to_bool(value: str) -> bool:
    """Convert text to boolean."""
    if value:
        return value.lower() == "true"
    return False


def bool_to_str(value: bool) -> str:
    """Convert boolean to 'true' or 'false'."""
    return "true" if value else "false"


def get_stream_id(channel_id: str, stream_type: int = 1) -> int:
    """Get stream id."""
    return int(channel_id) * 100 + stream_type


def deep_get(dictionary: dict, path: str, default: Any = None) -> Any:
    """Get safely nested dictionary attribute"""
    return reduce(
        lambda d, key: d.get(key, default) if isinstance(d, dict) else default,
        path.split("."),
        dictionary,
    )
