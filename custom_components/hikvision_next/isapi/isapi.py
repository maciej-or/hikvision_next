"""Hikvision ISAPI client."""

from __future__ import annotations

from contextlib import suppress
import datetime
from http import HTTPStatus
import ipaddress
import json
import logging
from typing import Any, AsyncIterator
from urllib.parse import quote, urljoin, urlparse

import httpx
from httpx import HTTPStatusError
import xmltodict

from .const import (
    CONNECTION_TYPE_DIRECT,
    CONNECTION_TYPE_PROXIED,
    EVENT_BASIC,
    EVENT_IO,
    EVENT_PIR,
    EVENTS,
    EVENTS_ALTERNATE_ID,
    GET,
    MUTEX_ALTERNATE_ID,
    POST,
    PUT,
    STREAM_TYPE,
)
from .models import (
    AlarmServer,
    AlertInfo,
    AnalogCamera,
    CameraStreamInfo,
    CapabilitiesInfo,
    EventInfo,
    IPCamera,
    ISAPIDeviceInfo,
    MutexIssue,
    ProtocolsInfo,
    StorageInfo,
)
from .utils import bool_to_str, deep_get, parse_isapi_response, str_to_bool

Node = dict[str, Any]

_LOGGER = logging.getLogger(__name__)


class ISAPIClient:
    """Hikvision ISAPI client."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool = True,
        rtsp_port_forced: int = None,
        session: httpx.AsyncClient = None,
    ) -> None:
        """Initialize."""

        self.host = host
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = 20
        self.isapi_prefix = "ISAPI"
        self._session = session
        self._auth_method: httpx._auth.Auth = None

        self.rtsp_port_forced = rtsp_port_forced

        self.device_info = ISAPIDeviceInfo()
        self.capabilities = CapabilitiesInfo()
        self.cameras: list[IPCamera | AnalogCamera] = []
        self.supported_events: list[EventInfo] = []
        self.storage: list[StorageInfo] = []
        self.protocols = ProtocolsInfo()
        self.pending_initialization = False

    async def get_device_info(self):
        """Get device info."""
        hw_info = (await self.request(GET, "System/deviceInfo")).get("DeviceInfo", {})
        self.device_info = ISAPIDeviceInfo(
            name=hw_info.get("deviceName"),
            manufacturer=str(hw_info.get("manufacturer", "Hikvision")).title(),
            model=hw_info.get("model"),
            serial_no=hw_info.get("serialNumber"),
            firmware=hw_info.get("firmwareVersion"),
            mac_address=hw_info.get("macAddress"),
            ip_address=urlparse(self.host).hostname,
            device_type=hw_info.get("deviceType"),
        )

    async def get_hardware_info(self):
        """Get device all data."""
        await self.get_device_info()
        capabilities = (await self.request(GET, "System/capabilities")).get("DeviceCap", {})

        self.capabilities.analog_cameras_inputs = int(deep_get(capabilities, "SysCap.VideoCap.videoInputPortNums", 0))
        self.capabilities.digital_cameras_inputs = int(deep_get(capabilities, "RacmCap.inputProxyNums", 0))
        self.capabilities.support_holiday_mode = str_to_bool(deep_get(capabilities, "SysCap.isSupportHolidy", "false"))
        self.capabilities.support_channel_zero = str_to_bool(
            deep_get(capabilities, "RacmCap.isSupportZeroChan", "false")
        )
        self.capabilities.support_event_mutex_checking = str_to_bool(
            capabilities.get("isSupportGetmutexFuncErrMsg", "false")
        )
        self.capabilities.input_ports = int(deep_get(capabilities, "SysCap.IOCap.IOInputPortNums", 0))
        self.capabilities.output_ports = int(deep_get(capabilities, "SysCap.IOCap.IOOutputPortNums", 0))
        self.capabilities.support_alarm_server = bool(await self.get_alarm_server())

        # Set if NVR based on whether more than 1 supported IP or analog cameras
        # Single IP camera will show 0 supported devices in total
        if self.capabilities.analog_cameras_inputs + self.capabilities.digital_cameras_inputs > 1:
            self.device_info.is_nvr = True

        await self.get_cameras()

        self.supported_events = await self.get_supported_events(capabilities)

        await self.get_protocols()

        with suppress(Exception):
            self.storage = await self.get_storage_devices()

    async def get_cameras(self):
        """Get camera objects for all connected cameras."""

        if not self.device_info.is_nvr:
            # Fetch the list of streaming channels (cameras), can be multiple for thermal cameras for example
            streaming_channels = await self.request(GET, "Streaming/channels")
            streaming_channel_list = deep_get(streaming_channels, "StreamingChannelList.StreamingChannel", [])

            channel_ids = set()
            for streaming_channel in streaming_channel_list:
                channel_id = int(deep_get(streaming_channel, "Video.videoInputChannelID", 1))
                channel_ids.add(channel_id)

            self.capabilities.is_multi_channel = len(channel_ids) > 1
            for channel_id in sorted(channel_ids):
                # Determine camera name
                if len(channel_ids) > 1:
                    camera_name = f"{self.device_info.name} - Channel {channel_id}"
                else:
                    camera_name = self.device_info.name

                camera = IPCamera(
                    id=channel_id,
                    name=camera_name,
                    model=self.device_info.model,
                    serial_no=self.device_info.serial_no,
                    firmware=self.device_info.firmware,
                    input_port=channel_id,
                    connection_type=CONNECTION_TYPE_DIRECT,
                    ip_addr=self.device_info.ip_address,
                    streams=await self.get_camera_streams(channel_id),
                )
                self.cameras.append(camera)
        else:
            # Get analog and digital cameras attached to NVR
            if self.capabilities.digital_cameras_inputs > 0:
                digital_cameras = deep_get(
                    (await self.request(GET, "ContentMgmt/InputProxy/channels")),
                    "InputProxyChannelList.InputProxyChannel",
                    [],
                )

                for digital_camera in digital_cameras:
                    camera_id = digital_camera.get("id")
                    source = digital_camera.get("sourceInputPortDescriptor")
                    if not source:
                        continue

                    serial_no = source.get("serialNumber")
                    if not serial_no or self.get_camera_by_serial_no(serial_no):
                        # serial no is not always recognized correcly by NVR
                        serial_no = f"{self.device_info.serial_no}_{source.get("proxyProtocol")}_{camera_id}"

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
                        )
                    )

            # Get analog cameras
            if self.capabilities.analog_cameras_inputs > 0:
                analog_cameras = deep_get(
                    (await self.request(GET, "System/Video/inputs/channels")),
                    "VideoInputChannelList.VideoInputChannel",
                    [],
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
                        )
                    )

    async def get_protocols(self):
        """Get protocols and ports."""
        protocols = deep_get(
            await self.request(GET, "Security/adminAccesses"),
            "AdminAccessProtocolList.AdminAccessProtocol",
            [],
        )

        for item in protocols:
            if item.get("protocol") == "RTSP" and item.get("portNo"):
                if self.rtsp_port_forced:
                    self.protocols.rtsp_port = str(self.rtsp_port_forced)
                else:
                    self.protocols.rtsp_port = item.get("portNo")
                break

    async def get_supported_events(self, system_capabilities: dict) -> list[EventInfo]:
        """Get list of all supported events available."""

        def create_event_info(event_trigger: dict):
            notification_list = event_trigger.get("EventTriggerNotificationList", {}) or {}

            event_type = event_trigger.get("eventType")
            if not event_type:
                return None
            event_id = event_type.lower()
            # Translate to alternate IDs
            if event_id in EVENTS_ALTERNATE_ID:
                event_id = EVENTS_ALTERNATE_ID[event_id]

            if event_id == EVENT_PIR:
                is_supported = str_to_bool(deep_get(system_capabilities, "WLAlarmCap.isSupportPIR", False))
                if not is_supported:
                    return None

            channel_id = 0
            io_port = 0
            is_proxy = False

            if event_id == EVENT_IO:
                io_port = int(event_trigger.get("inputIOPortID", 0))
                if not io_port:
                    io_port = int(event_trigger.get("dynInputIOPortID", 0))
                    is_proxy = io_port > 0
            else:
                channel_id = int(event_trigger.get("videoInputChannelID", 0))
                if not channel_id:
                    channel_id = int(event_trigger.get("dynVideoInputChannelID", 0))
                    is_proxy = channel_id > 0

            url = self.get_event_url(event_id, channel_id, io_port, is_proxy)

            notifications = deep_get(notification_list, "EventTriggerNotification", [])

            return EventInfo(
                channel_id=channel_id,
                io_port_id=io_port,
                id=event_id,
                url=url,
                is_proxy=is_proxy,
                notifications=[notify.get("notificationMethod") for notify in notifications] if notifications else [],
            )

        events = []

        # Get events from Event/triggers
        event_triggers = await self.request(GET, "Event/triggers")
        event_notification = event_triggers.get("EventNotification")
        if event_notification:
            available_events = deep_get(event_notification, "EventTriggerList.EventTrigger", [])
        else:
            available_events = deep_get(event_triggers, "EventTriggerList.EventTrigger", [])

        for event_trigger in available_events:
            if event := create_event_info(event_trigger):
                events.append(event)

        # some devices do not have scenechangedetection in Event/triggers
        if not [e for e in events if e.id == "scenechangedetection"]:
            is_supported = str_to_bool(deep_get(system_capabilities, "SmartCap.isSupportSceneChangeDetection", False))
            if is_supported:
                event_trigger = await self.request(GET, "Event/triggers/scenechangedetection-1")
                event_trigger = deep_get(event_trigger, "EventTrigger", {})
                if event := create_event_info(event_trigger):
                    events.append(event)

        # multichannel camera needs to fetch events for each channel
        if self.capabilities.is_multi_channel:
            channels_capabilities = await self.request(GET, "Event/channels/capabilities")
            channel_events = deep_get(channels_capabilities, "ChannelEventCapList.ChannelEventCap", [])
            for event_cap in channel_events:
                event_types = deep_get(event_cap, "eventType").get("@opt", "").split(",")
                channel_id = int(event_cap.get("channelID"))
                for event_type in event_types:
                    event_id = event_type.lower()
                    if event_id in EVENTS_ALTERNATE_ID:
                        event_id = EVENTS_ALTERNATE_ID[event_id]
                    if event_id not in EVENTS:
                        continue
                    if not [e for e in events if (e.id == event_id and e.channel_id == channel_id)]:
                        event_trigger = await self.request(GET, f"Event/triggers/{event_id}-{channel_id}")
                        event_trigger = deep_get(event_trigger, "EventTrigger", {})
                        if event := create_event_info(event_trigger):
                            events.append(event)

        return events

    def get_event_url(self, event_id: str, channel_id: int, io_port_id: int, is_proxy: bool) -> str | None:
        """Get event ISAPI URL."""

        if not EVENTS.get(event_id):
            return None

        event_type = EVENTS[event_id]["type"]
        slug = EVENTS[event_id]["slug"]

        if event_type == EVENT_BASIC:
            if is_proxy:
                url = f"ContentMgmt/InputProxy/channels/{channel_id}/video/{slug}"
            else:
                url = f"System/Video/inputs/channels/{channel_id}/{slug}"

        elif event_type == EVENT_IO:
            if is_proxy:
                url = f"ContentMgmt/IOProxy/{slug}/{io_port_id}"
            else:
                url = f"System/IO/{slug}/{io_port_id}"
        elif event_type == EVENT_PIR:
            # ISAPI/WLAlarm/PIR
            url = slug
        else:
            url = f"Smart/{slug}/{channel_id}"
        return url

    async def get_camera_streams(self, channel_id: int) -> list[CameraStreamInfo]:
        """Get stream info for all cameras."""
        streams = []
        for stream_type_id, stream_type in STREAM_TYPE.items():
            stream_id = f"{channel_id}0{stream_type_id}"
            stream_info = (await self.request(GET, f"Streaming/channels/{stream_id}")).get("StreamingChannel")
            if not stream_info:
                continue
            streams.append(
                CameraStreamInfo(
                    id=int(stream_info["id"]),
                    name=stream_info["channelName"],
                    type_id=stream_type_id,
                    type=stream_type,
                    enabled=stream_info["enabled"],
                    codec=deep_get(stream_info, "Video.videoCodecType"),
                    width=deep_get(stream_info, "Video.videoResolutionWidth", 0),
                    height=deep_get(stream_info, "Video.videoResolutionHeight", 0),
                    audio=str_to_bool(deep_get(stream_info, "Audio.enabled", "false")),
                )
            )
        return streams

    def get_camera_by_id(self, camera_id: int) -> IPCamera | AnalogCamera | None:
        """Get camera object by id."""
        try:
            if camera_id == 0:
                return None
            return [camera for camera in self.cameras if camera.id == camera_id][0]
        except IndexError:
            # Camera id does not exist
            return None

    def get_camera_by_serial_no(self, serial_no: str) -> IPCamera | AnalogCamera | None:
        """Get camera object by serial number."""
        for c in self.cameras:
            if c.serial_no == serial_no:
                return c
        return None

    async def get_storage_devices(self):
        """Get HDD and NAS storage devices."""
        storage_list = []
        storage_info = (await self.request(GET, "ContentMgmt/Storage")).get("storage", {})

        hdd_list = storage_info.get("hddList") or {}
        if "hdd" in hdd_list:
            if not isinstance(hdd_list, list):
                hdd_list = [hdd_list]
            for storage in hdd_list:
                storage = storage.get("hdd")
                if not isinstance(storage, list):
                    storage = [storage]
                if storage:
                    for item in storage:
                        storage_list.append(  # noqa: PERF401
                            StorageInfo(
                                id=int(item.get("id")),
                                name=item.get("hddName"),
                                type=item.get("hddType"),
                                status=item.get("status"),
                                capacity=int(item.get("capacity")),
                                freespace=int(item.get("freeSpace")),
                                property=item.get("property"),
                            )
                        )

        nas_list = storage_info.get("nasList") or {}
        if "nas" in nas_list:
            if not isinstance(nas_list, list):
                nas_list = [nas_list]
            for storage in nas_list:
                storage = storage.get("nas")
                if not isinstance(storage, list):
                    storage = [storage]
                if storage:
                    for item in storage:
                        storage_list.append(  # noqa: PERF401
                            StorageInfo(
                                id=int(item.get("id")),
                                name=item.get("path"),
                                type=item.get("nasType"),
                                status=item.get("status"),
                                capacity=int(item.get("capacity")),
                                freespace=int(item.get("freeSpace")),
                                property=item.get("property"),
                                ip=item.get("ipAddress"),
                            )
                        )

        return storage_list

    def get_storage_device_by_id(self, device_id: int) -> StorageInfo | None:
        """Get storage object by id."""
        try:
            return [storage_device for storage_device in self.storage if storage_device.id == device_id][0]
        except IndexError:
            # Storage id does not exist
            return None

    def _get_event_state_node(self, event: EventInfo) -> str:
        """Get xml key for event state."""
        slug = EVENTS[event.id]["slug"]

        # Alternate node name for some event types
        if event.is_proxy and (proxied_node := EVENTS[event.id].get("proxied_node")):
            slug = proxied_node
        if not event.is_proxy and (direct_node := EVENTS[event.id].get("direct_node")):
            slug = direct_node

        return slug[0].upper() + slug[1:]

    async def get_event_enabled_state(self, event: EventInfo) -> bool:
        """Get event detection state."""
        if not event.url:
            _LOGGER.warning("Cannot fetch event enabled state. Unknown event URL %s", event.id)
            return False
        state = await self.request(GET, event.url)
        node = self._get_event_state_node(event)
        return str_to_bool(state[node].get("enabled", "false")) if state.get(node) else False

    async def get_event_switch_mutex(self, event: EventInfo, channel_id: int) -> list[MutexIssue]:
        """Get if event is mutually exclusive with enabled events."""
        mutex_issues = []

        if not EVENTS[event.id].get("mutex"):
            return mutex_issues

        # Use alt event ID for mutex due to crap API!
        event_id = event.id
        if MUTEX_ALTERNATE_ID.get(event.id):
            event_id = MUTEX_ALTERNATE_ID[event.id]

        data = {"function": event_id, "channelID": int(channel_id)}
        url = "System/mutexFunction?format=json"
        response = await self.request(POST, url, present="json", data=json.dumps(data))
        if not response:
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
        if not event.url:
            _LOGGER.warning("Cannot set event enabled state. Unknown event URL %s", event.id)
            return False
        # Validate that this event switch is not mutually exclusive with another enabled one
        mutex_issues = []
        if channel_id != 0 and is_enabled and self.capabilities.support_event_mutex_checking:
            mutex_issues = await self.get_event_switch_mutex(event, channel_id)

        if not mutex_issues:
            data = await self.request(GET, event.url)
            node = self._get_event_state_node(event)
            new_state = bool_to_str(is_enabled)
            if new_state == data[node]["enabled"]:
                return
            data[node]["enabled"] = new_state
            xml = xmltodict.unparse(data)
            await self.request(PUT, event.url, present="xml", data=xml)
        else:
            raise ISAPISetEventStateMutexError(event, mutex_issues)

    async def get_io_port_status(self, port_type: str, port_no: int) -> str:
        """Get status of physical ports."""
        if port_type == "input":
            status = await self.request(GET, f"System/IO/inputs/{port_no}/status")
        else:
            status = await self.request(GET, f"System/IO/outputs/{port_no}/status")
        return deep_get(status, "IOPortStatus.ioState", "inactive") == "active"

    async def set_output_port_state(self, port_no: int, turn_on: bool):
        """Set status of output port."""
        data = {}
        if turn_on:
            data["IOPortData"] = {"outputState": "high"}
        else:
            data["IOPortData"] = {"outputState": "low"}

        xml = xmltodict.unparse(data)
        await self.request(PUT, f"System/IO/outputs/{port_no}/trigger", present="xml", data=xml)

    async def get_holiday_enabled_state(self, holiday_index=0) -> bool:
        """Get holiday state."""

        data = await self.request(GET, "System/Holidays")
        holiday = data["HolidayList"]["holiday"][holiday_index]
        return str_to_bool(holiday["enabled"]["#text"])

    async def set_holiday_enabled_state(self, is_enabled: bool, holiday_index=0) -> None:
        """Enable or disable holiday, by enable set time span to year starting from today."""

        data = await self.request(GET, "System/Holidays")
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
        await self.request(PUT, "System/Holidays", present="xml", data=xml)

    def _get_event_notification_host(self, data: Node) -> Node:
        hosts = deep_get(data, "HttpHostNotificationList.HttpHostNotification", [])
        if hosts:
            return hosts[0]

    async def get_alarm_server(self) -> AlarmServer | None:
        """Get event notifications listener server URL."""

        data = await self.request(GET, "Event/notification/httpHosts")
        if not data:
            return None
        host = self._get_event_notification_host(data)

        return AlarmServer(
            ip_address=host.get("ipAddress"),
            port_no=int(host.get("portNo")),
            url=host.get("url"),
            protocol_type=host.get("protocolType"),
            host_name=host.get("hostName"),
        )

    async def set_alarm_server(self, base_url: str, path: str) -> None:
        """Set event notifications listener server."""

        address = urlparse(base_url)
        data = await self.request(GET, "Event/notification/httpHosts")
        if not data:
            return
        host = self._get_event_notification_host(data)

        old_address = ""
        if host.get("addressingFormatType") == "ipaddress":
            old_address = host.get("ipAddress")
        else:
            old_address = host.get("hostname")

        if (
            host["protocolType"] == address.scheme.upper()
            and old_address == address.hostname
            and host.get("portNo") == str(address.port)
            and host["url"] == path
        ):
            return
        host["url"] = path
        host["protocolType"] = address.scheme.upper()
        host["parameterFormatType"] = "XML"

        try:
            ipaddress.ip_address(address.hostname)

            # if address.hostname is an ip
            host["addressingFormatType"] = "ipaddress"
            host["ipAddress"] = address.hostname
            host["hostName"] = None
            del host["hostName"]
        except ValueError:
            # if address.hostname is a domain
            host["addressingFormatType"] = "hostname"
            host["ipAddress"] = None
            del host["ipAddress"]
            host["hostName"] = address.hostname

        host["portNo"] = address.port or (443 if address.scheme == "https" else 80)
        host["httpAuthenticationMethod"] = "none"

        xml = xmltodict.unparse(data)
        await self.request(PUT, "Event/notification/httpHosts", present="xml", data=xml)

    async def reboot(self):
        """Reboot device."""
        await self.request(PUT, "System/reboot", present="xml")

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

        detection_target = deep_get(alert, "DetectionRegionList.DetectionRegionEntry.detectionTarget")
        region_id = int(deep_get(alert, "DetectionRegionList.DetectionRegionEntry.regionID", 0))

        if not EVENTS[event_id]:
            raise ValueError(f"Unsupported event {event_id}")

        return AlertInfo(
            channel_id,
            io_port_id,
            event_id,
            device_serial,
            mac,
            region_id,
            detection_target,
        )

    async def get_camera_image(
        self,
        stream: CameraStreamInfo,
        width: int | None = None,
        height: int | None = None,
        attempt: int = 0,
    ):
        """Get camera snapshot."""
        params = {}
        if not width or width > 100:
            params = {
                "videoResolutionWidth": stream.width,
                "videoResolutionHeight": stream.height,
            }

        if stream.use_alternate_picture_url:
            url = f"ContentMgmt/StreamingProxy/channels/{stream.id}/picture"
            full_url = self.get_isapi_url(url)
            chunks = self.request_bytes(GET, full_url, params=params)
        else:
            url = f"Streaming/channels/{stream.id}/picture"
            full_url = self.get_isapi_url(url)
            chunks = self.request_bytes(GET, full_url, params=params)
        data = b"".join([chunk async for chunk in chunks])

        if data.startswith(b"<?xml "):
            error = xmltodict.parse(data)
            status_code = int(deep_get(error, "ResponseStatus.statusCode"))
            if status_code == 6 and not stream.use_alternate_picture_url:
                # handle 'Invalid XML Content' for some cameras, use alternate url for still image
                stream.use_alternate_picture_url = True
                return await self.get_camera_image(stream, width, height)
            if status_code == 3 and attempt < 2:
                # handle 'Device Error', try again
                return await self.get_camera_image(stream, width, height, attempt + 1)

        return data

    def get_stream_source(self, stream: CameraStreamInfo) -> str:
        """Get stream source."""
        u = quote(self.username, safe="")
        p = quote(self.password, safe="")
        url = f"{self.device_info.ip_address}:{self.protocols.rtsp_port}/Streaming/channels/{stream.id}"
        return f"rtsp://{u}:{p}@{url}"

    async def _detect_auth_method(self):
        """Establish the connection with device."""
        if not self._session:
            self._session = httpx.AsyncClient(timeout=self.timeout, verify=self.verify_ssl)

        url = urljoin(self.host, self.isapi_prefix + "/System/deviceInfo")
        _LOGGER.debug("--- [WWW-Authenticate detection] %s", self.host)
        response = await self._session.get(url)
        if response.status_code == 401:
            www_authenticate = response.headers.get("WWW-Authenticate", "")
            _LOGGER.debug("WWW-Authenticate header: %s", www_authenticate)
            if "Basic" in www_authenticate:
                self._auth_method = httpx.BasicAuth(self.username, self.password)
            elif "Digest" in www_authenticate:
                self._auth_method = httpx.DigestAuth(self.username, self.password)

        if not self._auth_method:
            _LOGGER.error("Authentication method not detected, %s", response.status_code)
            if response.headers:
                _LOGGER.error("response.headers %s", response.headers)

    def get_isapi_url(self, relative_url: str) -> str:
        """Build full ISAPI URL."""
        return f"{self.host}/{self.isapi_prefix}/{relative_url}"

    async def request(
        self,
        method: str,
        url: str,
        present: str = "dict",
        data: str = None,
    ) -> Any:
        """Send ISAPI request and log response, returns {} if request fails."""
        full_url = self.get_isapi_url(url)
        try:
            if not self._auth_method:
                await self._detect_auth_method()

            response = await self._session.request(
                method,
                full_url,
                auth=self._auth_method,
                data=data,
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = parse_isapi_response(response, present)
            _LOGGER.debug("--- [%s] %s", method, full_url)
            if data:
                _LOGGER.debug(">>> payload:\n%s", data)
            _LOGGER.debug("\n%s", result)
        except HTTPStatusError as ex:
            _LOGGER.info("--- [%s] %s\n%s", method, full_url, ex)
            if ex.response.status_code == HTTPStatus.UNAUTHORIZED:
                raise ISAPIUnauthorizedError(ex) from ex
            if ex.response.status_code == HTTPStatus.FORBIDDEN and not self.pending_initialization:
                raise ISAPIForbiddenError(ex) from ex
            if self.pending_initialization:
                # supress http errors during initialization
                return {}
            raise
        else:
            return result

    async def request_bytes(
        self,
        method: str,
        full_url: str,
        **data,
    ) -> AsyncIterator[bytes]:
        """Send ISAPI request for binary data."""

        try:
            if not self._auth_method:
                await self._detect_auth_method()

            async with self._session.stream(method, full_url, auth=self._auth_method, **data) as response:
                async for chunk in response.aiter_bytes():
                    yield chunk
        except httpx.HTTPError as ex:
            _LOGGER.warning("Failed request [%s] %s | %s", method, full_url, ex)


class ISAPISetEventStateMutexError(Exception):
    """Error setting event mutex."""

    def __init__(self, event: EventInfo, mutex_issues: []) -> None:
        """Initialize exception."""
        self.event = event
        self.mutex_issues = mutex_issues
        self.message = f"""You cannot enable {EVENTS[event.id]['label']} events.
            Please disable {EVENTS[mutex_issues[0].event_id]['label']}
            on channels {mutex_issues[0].channels} first"""


class ISAPIUnauthorizedError(Exception):
    """HTTP Error 401."""

    def __init__(self, ex: HTTPStatusError, *args) -> None:
        """Initialize exception."""
        self.message = f"Unauthorized request {ex.request.url}, check username and password."
        self.response = ex.response


class ISAPIForbiddenError(Exception):
    """HTTP Error 403."""

    def __init__(self, ex: HTTPStatusError, *args) -> None:
        """Initialize exception."""
        self.message = f"Forbidden request {ex.request.url}, check user permissions."
        self.response = ex.response
        _LOGGER.warning(self.message)
