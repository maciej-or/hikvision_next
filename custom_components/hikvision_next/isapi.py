"""Hikvision ISAPI client."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import datetime
from functools import reduce
from http import HTTPStatus
import json
import logging
from typing import Any, Optional
from urllib.parse import quote, urlparse

import httpx
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
    EVENT_PIR,
    EVENTS,
    EVENTS_ALTERNATE_ID,
    MUTEX_ALTERNATE_IDS,
    STREAM_TYPE,
)
from .isapi_client import ISAPI_Client

Node = dict[str, Any]

_LOGGER = logging.getLogger(__name__)

GET = "GET"
PUT = "PUT"
POST = "POST"


@dataclass
class AlarmServer:
    """Holds alarm server info."""

    # Uses pylint invalid names to not break previous versions
    ipAddress: str  # pylint: disable=invalid-name
    portNo: int  # pylint: disable=invalid-name
    url: str  # pylint: disable=invalid-name
    protocolType: str  # pylint: disable=invalid-name


@dataclass
class AlertInfo:
    """Holds NVR/Camera event notification info."""

    channel_id: int
    io_port_id: int
    event_id: str
    device_serial_no: Optional[str]
    mac: str = ""
    region_id: int = 0
    detection_target: Optional[str] = None


@dataclass
class MutexIssue:
    """Holds mutually exclusive event checking info."""

    event_id: str
    channels: list = field(default_factory=list)


@dataclass
class EventInfo:
    """Holds event info of particular device."""

    id: str
    channel_id: int
    io_port_id: int
    unique_id: str
    url: str
    disabled: bool = False


@dataclass
class SupportedEventsInfo:
    """Holds supported event info for NVR/IP Camera."""

    channel_id: int
    io_port_id: int
    event_id: str
    notifications: list[str] = field(default_factory=list)


@dataclass
class CameraStreamInfo:
    """Holds info of a camera stream."""

    id: int
    name: str
    type_id: int
    type: str
    enabled: bool
    codec: str
    width: int
    height: int
    audio: bool
    use_alternate_picture_url: bool = False


@dataclass
class StorageInfo:
    """Holds info for internal and NAS storage devices."""

    id: int
    name: str
    type: str
    status: str
    capacity: int
    freespace: int
    property: str
    ip: str = ""


@dataclass
class HikDeviceInfo:
    """Holds info of an NVR/DVR or single IP Camera."""

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
    storage: list[StorageInfo] = field(default_factory=list)
    events_info: list[EventInfo] = field(default_factory=list)


@dataclass
class AnalogCamera:
    """Analog cameras info."""

    id: int
    name: str
    model: str
    serial_no: str
    input_port: int
    connection_type: str
    streams: list[CameraStreamInfo] = field(default_factory=list)
    events_info: list[EventInfo] = field(default_factory=list)
    channel_id: int = 0  # Added attribute to map channel_id


@dataclass
class IPCamera(AnalogCamera):
    """IP/Digital camera info."""

    firmware: str = ""
    ip_addr: str = ""
    ip_port: int = 0


class ISAPI:
    """Hikvision ISAPI async client wrapper."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        session: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """Initialize."""
        self.isapi = ISAPI_Client(host, username, password, session, timeout=20)
        self.host = host
        self.device_info = HikDeviceInfo()
        self.cameras: list[IPCamera | AnalogCamera] = []
        self.supported_events: list[SupportedEventsInfo] = []
        self.pending_initialization = False
        _LOGGER.debug("ISAPI client initialized with host: %s", host)

    async def get_device_info(self):
        """Get device info."""
        _LOGGER.debug("Fetching device info from 'System/deviceInfo'")
        hw_info = (await self.request(GET, "System/deviceInfo")).get("DeviceInfo", {})
        _LOGGER.debug("Device Info retrieved: %s", hw_info)
        self.device_info = HikDeviceInfo(
            name=hw_info.get("deviceName"),
            manufacturer=str(hw_info.get("manufacturer", "Hikvision")).title(),
            model=hw_info.get("model"),
            serial_no=hw_info.get("serialNumber"),
            firmware=hw_info.get("firmwareVersion"),
            mac_address=hw_info.get("macAddress"),
            ip_address=urlparse(self.host).hostname,
            device_type=hw_info.get("deviceType"),
        )
        _LOGGER.debug("Parsed HikDeviceInfo: %s", self.device_info)

    async def get_hardware_info(self):
        """Get base device data."""
        _LOGGER.debug("Starting hardware information retrieval")
        # Get base hw info
        await self.get_device_info()

        # Get device capabilities
        _LOGGER.debug("Fetching device capabilities from 'System/capabilities'")
        capabilities = (await self.request(GET, "System/capabilities")).get("DeviceCap", {})
        _LOGGER.debug("Device capabilities retrieved: %s", capabilities)

        # Get all supported events to reduce isapi queries
        _LOGGER.debug("Fetching supported events")
        self.supported_events = await self.get_supported_events(capabilities)
        _LOGGER.debug("Supported events: %s", self.supported_events)

        # Set DeviceInfo attributes
        self.device_info.support_analog_cameras = int(
            deep_get(capabilities, "SysCap.VideoCap.videoInputPortNums", 0)
        )
        self.device_info.support_digital_cameras = int(
            deep_get(capabilities, "RacmCap.inputProxyNums", 0)
        )
        self.device_info.support_holiday_mode = str_to_bool(
            deep_get(capabilities, "SysCap.isSupportHolidy", "false")
        )
        self.device_info.support_channel_zero = str_to_bool(
            deep_get(capabilities, "RacmCap.isSupportZeroChan", "false")
        )
        self.device_info.support_event_mutex_checking = str_to_bool(
            capabilities.get("isSupportGetmutexFuncErrMsg", "false")
        )
        self.device_info.input_ports = int(
            deep_get(capabilities, "SysCap.IOCap.IOInputPortNums", 0)
        )
        self.device_info.output_ports = int(
            deep_get(capabilities, "SysCap.IOCap.IOOutputPortNums", 0)
        )

        with suppress(Exception):
            _LOGGER.debug("Fetching storage devices")
            self.device_info.storage = await self.get_storage_devices()
            _LOGGER.debug("Storage devices retrieved: %s", self.device_info.storage)

        _LOGGER.debug("Fetching alarm server information")
        self.device_info.support_alarm_server = bool(await self.get_alarm_server())
        _LOGGER.debug("Support alarm server: %s", self.device_info.support_alarm_server)

        _LOGGER.debug("Fetching protocols")
        await self.get_protocols()

        # Set if NVR based on whether more than 1 supported IP or analog cameras
        # Single IP camera will show 0 supported devices in total
        total_cameras = (
            self.device_info.support_analog_cameras + self.device_info.support_digital_cameras
        )
        _LOGGER.debug("Total supported cameras: %d", total_cameras)
        if total_cameras > 1:
            self.device_info.is_nvr = True
            _LOGGER.debug("Device is identified as NVR")
        else:
            _LOGGER.debug("Device is identified as single IP camera")

        # Fetch event capabilities for NVR if applicable
        if self.device_info.is_nvr:
            _LOGGER.debug("Fetching device event capabilities for NVR")
            self.device_info.events_info = await self.get_device_event_capabilities(
                self.supported_events, self.device_info.serial_no, 0
            )
            _LOGGER.debug("Device events_info: %s", self.device_info.events_info)

    async def get_cameras(self):
        """Get camera objects for all connected cameras."""
        _LOGGER.debug("Starting to fetch camera information")
        if not self.device_info.is_nvr:
            _LOGGER.debug("Device is not NVR, fetching channels")
            # Fetch the list of streaming channels
            streaming_channels = await self.request(GET, "Streaming/channels")
            streaming_channel_list = streaming_channels.get("StreamingChannelList", {}).get("StreamingChannel", [])
            _LOGGER.debug("Streaming channels raw data: %s", streaming_channel_list)

            if not isinstance(streaming_channel_list, list):
                streaming_channel_list = [streaming_channel_list]

            channel_ids = set()
            for streaming_channel in streaming_channel_list:
                stream_id = int(streaming_channel["id"])
                channel_id = stream_id // 100  # Channel ID is the integer division of stream_id by 100
                channel_ids.add(channel_id)

            _LOGGER.debug("Detected channels: %s", channel_ids)

            for channel_id in sorted(channel_ids):
                # Determine the camera name
                if len(channel_ids) > 1:
                    # For multi-channel cameras, include channel information in the name
                    camera_name = f"{self.device_info.name} - Channel {channel_id}"
                    serial_no = f"{self.device_info.serial_no}-CH{channel_id}"
                else:
                    # For single-channel cameras, keep the original name
                    camera_name = self.device_info.name
                    serial_no = self.device_info.serial_no

                camera = IPCamera(
                    id=channel_id,
                    name=camera_name,
                    model=self.device_info.model,
                    serial_no=serial_no,
                    firmware=self.device_info.firmware,
                    input_port=channel_id,
                    connection_type=CONNECTION_TYPE_DIRECT,
                    ip_addr=self.device_info.ip_address,
                    streams=await self.get_camera_streams(channel_id),
                    events_info=[],
                    channel_id=channel_id,
                )
                _LOGGER.debug("Camera details: %s", camera)
                self.cameras.append(camera)

            # Now fetch events_info for each camera
            for camera in self.cameras:
                camera.events_info = await self.get_device_event_capabilities(
                    self.supported_events,
                    camera.serial_no,
                    camera.id,
                    CONNECTION_TYPE_DIRECT,
                )
        else:
            _LOGGER.debug("Device is NVR, fetching attached digital and analog cameras")
            # Get analog and digital cameras attached to NVR
            if self.device_info.support_digital_cameras > 0:
                _LOGGER.debug("Fetching digital cameras from 'ContentMgmt/InputProxy/channels'")
                digital_cameras = deep_get(
                    (await self.request(GET, "ContentMgmt/InputProxy/channels")),
                    "InputProxyChannelList.InputProxyChannel",
                    [],
                )
                _LOGGER.debug("Digital cameras raw data: %s", digital_cameras)

                if not isinstance(digital_cameras, list):
                    digital_cameras = [digital_cameras]

                for digital_camera in digital_cameras:
                    camera_id = digital_camera.get("id")
                    source = digital_camera.get("sourceInputPortDescriptor")
                    _LOGGER.debug("Processing digital camera ID: %s, Source: %s", camera_id, source)
                    if not source:
                        _LOGGER.warning("Source input port descriptor missing for camera ID: %s", camera_id)
                        continue

                    # Generate serial number if not provided by camera
                    serial_no = source.get("serialNumber")
                    if not serial_no:
                        serial_no = (
                            str(source.get("proxyProtocol"))
                            + str(source.get("ipAddress", "")).replace(".", "")
                        )
                        _LOGGER.debug(
                            "Generated serial number for digital camera ID %s: %s", camera_id, serial_no
                        )

                    # Determine channel_id based on camera_id or other attributes
                    # Assuming camera_id maps directly to channel_id; adjust if necessary
                    channel_id = int(camera_id)

                    ip_camera = IPCamera(
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
                        events_info=[],
                        channel_id=channel_id,
                    )
                    _LOGGER.debug("Digital camera added: %s", ip_camera)
                    self.cameras.append(ip_camera)
                # Now fetch events_info for each digital camera
                for camera in self.cameras:
                    camera.events_info = await self.get_device_event_capabilities(
                        self.supported_events,
                        camera.serial_no,
                        camera.id,
                        camera.connection_type,
                    )

            if self.device_info.support_analog_cameras > 0:
                _LOGGER.debug("Fetching analog cameras from 'System/Video/inputs/channels'")
                analog_cameras = deep_get(
                    (await self.request(GET, "System/Video/inputs/channels")),
                    "VideoInputChannelList.VideoInputChannel",
                    [],
                )
                _LOGGER.debug("Analog cameras raw data: %s", analog_cameras)

                if not isinstance(analog_cameras, list):
                    analog_cameras = [analog_cameras]

                for analog_camera in analog_cameras:
                    camera_id = analog_camera.get("id")
                    device_serial_no = f"{self.device_info.serial_no}-VI{camera_id}"
                    _LOGGER.debug(
                        "Processing analog camera ID: %s, Serial No: %s", camera_id, device_serial_no
                    )

                    # Determine channel_id based on camera_id or other attributes
                    # Assuming camera_id maps directly to channel_id; adjust if necessary
                    channel_id = int(camera_id)

                    analog_cam = AnalogCamera(
                        id=int(camera_id),
                        name=analog_camera.get("name"),
                        model=analog_camera.get("resDesc"),
                        serial_no=device_serial_no,
                        input_port=int(analog_camera.get("inputPort")),
                        connection_type=CONNECTION_TYPE_DIRECT,
                        streams=await self.get_camera_streams(camera_id),
                        events_info=[],
                        channel_id=channel_id,
                    )
                    _LOGGER.debug("Analog camera added: %s", analog_cam)
                    self.cameras.append(analog_cam)
                # Now fetch events_info for each analog camera
                for camera in self.cameras:
                    camera.events_info = await self.get_device_event_capabilities(
                        self.supported_events,
                        camera.serial_no,
                        camera.id,
                        camera.connection_type,
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
                self.device_info.rtsp_port = int(item.get("portNo"))
                break

    async def get_device_event_capabilities(
        self,
        supported_events: list[SupportedEventsInfo],
        serial_no: str,
        device_id: int,
        connection_type: str = CONNECTION_TYPE_DIRECT,
    ) -> list[EventInfo]:
        """Get events info handled by integration (device id: NVR = 0, camera > 0)."""
        _LOGGER.debug(
            "Fetching device event capabilities for serial_no: %s, device_id: %d, connection_type: %s",
            serial_no,
            device_id,
            connection_type,
        )
        events = []

        # Determine the correct channel_id based on device_id
        channel_id = None
        for camera in self.cameras:
            if camera.id == device_id:
                channel_id = camera.channel_id
                _LOGGER.debug("Mapped device_id: %d to channel_id: %d", device_id, channel_id)
                break

        if device_id == 0 and not self.device_info.is_nvr:
            # For non-NVR devices, device_id=0 may not be valid
            _LOGGER.error("Attempted to fetch event capabilities for device_id: 0, which is not an NVR.")
            return events

        if channel_id is None and device_id != 0:
            _LOGGER.error("No channel_id found for device_id: %d", device_id)
            return events

        if device_id == 0:  # NVR
            device_supported_events = [
                s
                for s in supported_events
                if (s.event_id.lower() in EVENTS and EVENTS[s.event_id.lower()].get("type") == EVENT_IO)
            ]
            _LOGGER.debug("NVR supported events after filtering: %s", device_supported_events)
        else:  # Camera
            device_supported_events = [
                s
                for s in supported_events
                if (s.channel_id == channel_id and s.event_id.lower() in EVENTS)
            ]
            _LOGGER.debug("Camera supported events after filtering: %s", device_supported_events)

        if not device_supported_events:
            _LOGGER.warning(
                "No device_supported_events found for device_id: %d, channel_id: %d. Available supported_events: %s",
                device_id,
                channel_id,
                supported_events,
            )

        for event in device_supported_events:
            _LOGGER.debug("Processing supported event: %s", event)
            # Build unique_id
            device_id_param = f"_{device_id}" if device_id != 0 else ""
            io_port_id_param = f"_{event.io_port_id}" if event.io_port_id != 0 else ""
            unique_id = f"{slugify(serial_no.lower())}{device_id_param}{io_port_id_param}_{event.event_id.lower()}"
            _LOGGER.debug("Generated unique_id for event: %s", unique_id)

            if EVENTS.get(event.event_id.lower()):
                event_info = EventInfo(
                    id=event.event_id.lower(),
                    channel_id=event.channel_id,
                    io_port_id=event.io_port_id,
                    unique_id=unique_id,
                    url=self.get_event_url(event, connection_type),
                    disabled=("center" not in event.notifications),
                )
                _LOGGER.debug("Created EventInfo: %s", event_info)
                events.append(event_info)
            else:
                _LOGGER.warning("Event ID '%s' not found in EVENTS configuration", event.event_id.lower())

        if not events:
            _LOGGER.warning("No events_info populated for device_id: %d, channel_id: %d", device_id, channel_id)
        else:
            _LOGGER.debug("Populated events_info: %s", events)

        return events

    async def get_supported_events(self, system_capabilities: dict) -> list[SupportedEventsInfo]:
        """Get list of all supported events available."""

        def get_event(event_trigger: dict):
            notification_list = event_trigger.get("EventTriggerNotificationList", {}) or {}
            event_type = event_trigger.get("eventType")
            if not event_type:
                return None

            if event_type.lower() == EVENT_PIR:
                is_supported = str_to_bool(
                    deep_get(system_capabilities, "WLAlarmCap.isSupportPIR", False)
                )
                if not is_supported:
                    return None

            channel = event_trigger.get("videoInputChannelID", event_trigger.get("dynVideoInputChannelID", 0))
            io_port = event_trigger.get("inputIOPortID", event_trigger.get("dynInputIOPortID", 0))
            notifications = notification_list.get("EventTriggerNotification", [])

            if not isinstance(notifications, list):
                notifications = [notifications]

            # Translate to alternate IDs
            if event_type.lower() in EVENTS_ALTERNATE_ID:
                event_type = EVENTS_ALTERNATE_ID[event_type.lower()]

            return SupportedEventsInfo(
                channel_id=int(channel),
                io_port_id=int(io_port),
                event_id=event_type.lower(),
                notifications=[
                    notify.get("notificationMethod") for notify in notifications
                ]
                if notifications
                else [],
            )

        _LOGGER.debug("Fetching supported events from 'Event/triggers'")
        events = []
        event_triggers = await self.request(GET, "Event/triggers")
        _LOGGER.debug("Event triggers raw data: %s", event_triggers)
        event_notification = event_triggers.get("EventNotification")
        if event_notification:
            supported_events = deep_get(event_notification, "EventTriggerList.EventTrigger", [])
            _LOGGER.debug(
                "Supported events extracted from EventNotification: %s", supported_events
            )
        else:
            supported_events = deep_get(event_triggers, "EventTriggerList.EventTrigger", [])
            _LOGGER.debug("Supported events extracted directly: %s", supported_events)

        if not isinstance(supported_events, list):
            supported_events = [supported_events]
            _LOGGER.debug("Converted supported_events to list: %s", supported_events)

        _LOGGER.debug("Number of supported_events to process: %d", len(supported_events))
        for event_trigger in supported_events:
            event_info = get_event(event_trigger)
            if event_info:
                _LOGGER.debug("Appending supported event: %s", event_info)
                events.append(event_info)
            else:
                _LOGGER.debug(
                    "Skipped unsupported or invalid event trigger: %s", event_trigger
                )

        # Handle scenechangedetection if not present
        if not any(e.event_id == "scenechangedetection" for e in events):
            is_supported = str_to_bool(
                deep_get(system_capabilities, "SmartCap.isSupportSceneChangeDetection", False)
            )
            _LOGGER.debug("Scene change detection supported: %s", is_supported)
            if is_supported:
                _LOGGER.debug("Fetching scenechangedetection event trigger")
                event_trigger = await self.request(GET, "Event/triggers/scenechangedetection-1")
                event_trigger = deep_get(event_trigger, "EventTrigger", {})
                _LOGGER.debug("Scene change detection event_trigger: %s", event_trigger)
                event_info = get_event(event_trigger)
                if event_info:
                    _LOGGER.debug("Appending scenechangedetection event: %s", event_info)
                    events.append(event_info)

        _LOGGER.debug("Final supported events list: %s", events)
        return events

    def get_event_url(self, event: SupportedEventsInfo, connection_type: str) -> str:
        """Get event ISAPI URL."""
        event_type = EVENTS[event.event_id]["type"]
        slug = EVENTS[event.event_id]["slug"]

        # Alternate node name for some event types
        if event.channel_id == 0:  # NVR
            if EVENTS[event.event_id].get("direct_node"):
                slug = EVENTS[event.event_id]["direct_node"]
        else:
            camera = self.get_camera_by_id(event.channel_id)
            if camera and camera.connection_type == CONNECTION_TYPE_DIRECT and EVENTS[event.event_id].get(
                "direct_node"
            ):
                slug = EVENTS[event.event_id]["direct_node"]

            if camera and camera.connection_type == CONNECTION_TYPE_PROXIED and EVENTS[event.event_id].get(
                "proxied_node"
            ):
                slug = EVENTS[event.event_id]["proxied_node"]

        # Build the URL based on the event type and slug
        if event_type == EVENT_BASIC:
            return f"System/Video/inputs/channels/{event.channel_id}/{slug}"
        elif event_type == EVENT_IO:
            return f"System/IO/inputs/channels/{event.io_port_id}/{slug}"
        else:
            # For other event types, adjust the URL as needed
            return f"System/Video/inputs/channels/{event.channel_id}/{slug}"

    async def get_camera_streams(self, channel_id: int) -> list[CameraStreamInfo]:
        """Get stream info for all cameras."""
        _LOGGER.debug("Fetching camera streams for channel_id: %d", channel_id)
        streams = []
        for stream_type_id, stream_type in STREAM_TYPE.items():
            stream_id = get_stream_id(str(channel_id), stream_type_id)
            _LOGGER.debug("Fetching stream ID: %d for channel_id: %d", stream_id, channel_id)
            stream_response = await self.request(GET, f"Streaming/channels/{stream_id}")
            stream_info = stream_response.get("StreamingChannel")
            if not stream_info:
                _LOGGER.warning(
                    "No stream info found for stream_id: %d on channel_id: %d",
                    stream_id,
                    channel_id,
                )
                continue
            streams.append(
                CameraStreamInfo(
                    id=int(stream_info["id"]),
                    name=stream_info["channelName"],
                    type_id=stream_type_id,
                    type=stream_type,
                    enabled=str_to_bool(stream_info["enabled"]),
                    codec=deep_get(stream_info, "Video.videoCodecType"),
                    width=int(deep_get(stream_info, "Video.videoResolutionWidth", 0)),
                    height=int(deep_get(stream_info, "Video.videoResolutionHeight", 0)),
                    audio=str_to_bool(deep_get(stream_info, "Audio.enabled", "false")),
                )
            )
            _LOGGER.debug("Added CameraStreamInfo: %s", streams[-1])
        return streams

    def get_camera_by_id(self, camera_id: int) -> IPCamera | AnalogCamera | None:
        """Get camera object by id."""
        try:
            return [camera for camera in self.cameras if camera.id == camera_id][0]
        except IndexError:
            # Camera id does not exist
            _LOGGER.warning("Camera with ID %d not found", camera_id)
            return None

    async def get_storage_devices(self):
        """Get HDD and NAS storage devices."""
        _LOGGER.debug("Fetching storage devices from 'ContentMgmt/Storage'")
        storage_list = []
        storage_info = (await self.request(GET, "ContentMgmt/Storage")).get("storage", {})

        # Handle HDDs
        hdd_list = storage_info.get("hddList", {})
        if "hdd" in hdd_list:
            hdds = hdd_list["hdd"]
            if not isinstance(hdds, list):
                hdds = [hdds]
            for item in hdds:
                storage_list.append(
                    StorageInfo(
                        id=int(item.get("id")),
                        name=item.get("hddName"),
                        type=item.get("hddType"),
                        status=item.get("status"),
                        capacity=int(item.get("capacity")),
                        freespace=int(item.get("freeSpace")),
                        property=item.get("property"),
                        ip=item.get("ipAddress", ""),
                    )
                )
                _LOGGER.debug("Added HDD StorageInfo: %s", storage_list[-1])

        # Handle NAS devices
        nas_list = storage_info.get("nasList", {})
        if "nas" in nas_list:
            nas_devices = nas_list["nas"]
            if not isinstance(nas_devices, list):
                nas_devices = [nas_devices]
            for item in nas_devices:
                storage_list.append(
                    StorageInfo(
                        id=int(item.get("id")),
                        name=item.get("path"),
                        type=item.get("nasType"),
                        status=item.get("status"),
                        capacity=int(item.get("capacity")),
                        freespace=int(item.get("freeSpace")),
                        property=item.get("property"),
                        ip=item.get("ipAddress", ""),
                    )
                )
                _LOGGER.debug("Added NAS StorageInfo: %s", storage_list[-1])

        _LOGGER.debug("Total storage devices retrieved: %d", len(storage_list))
        return storage_list

    def get_storage_device_by_id(self, device_id: int) -> StorageInfo | None:
        """Get storage object by id."""
        try:
            return [
                storage_device
                for storage_device in self.device_info.storage
                if storage_device.id == device_id
            ][0]
        except IndexError:
            # Storage id does not exist
            _LOGGER.warning("Storage device with ID %d not found", device_id)
            return None

    def hass_device_info(self, device_id: int = 0) -> DeviceInfo:
        """Return Home Assistant entity device information."""
        if device_id == 0:
            return DeviceInfo(
                manufacturer=self.device_info.manufacturer,
                identifiers={(DOMAIN, self.device_info.serial_no)},
                connections={
                    (dr.CONNECTION_NETWORK_MAC, self.device_info.mac_address)
                },
                model=self.device_info.model,
                name=self.device_info.name,
                sw_version=self.device_info.firmware,
            )
        else:
            camera_info = self.get_camera_by_id(device_id)
            if not camera_info:
                _LOGGER.error(
                    "Cannot generate DeviceInfo, camera with ID %d not found",
                    device_id,
                )
                return DeviceInfo()
            is_ip_camera = isinstance(camera_info, IPCamera)
            return DeviceInfo(
                manufacturer=self.device_info.manufacturer,
                identifiers={(DOMAIN, camera_info.serial_no)},
                model=camera_info.model,
                name=camera_info.name,
                sw_version=camera_info.firmware if is_ip_camera else "Unknown",
                via_device=(
                    (DOMAIN, self.device_info.serial_no)
                    if self.device_info.is_nvr
                    else None
                ),
            )

    def get_event_state_node(self, event: EventInfo) -> str:
        """Get XML key for event state."""
        slug = EVENTS[event.id]["slug"]

        # Alternate node name for some event types
        if event.channel_id == 0:  # NVR
            if EVENTS[event.id].get("direct_node"):
                slug = EVENTS[event.id]["direct_node"]
        else:
            camera = self.get_camera_by_id(event.channel_id)
            if camera and camera.connection_type == CONNECTION_TYPE_DIRECT and EVENTS[event.id].get(
                "direct_node"
            ):
                slug = EVENTS[event.id]["direct_node"]

            if camera and camera.connection_type == CONNECTION_TYPE_PROXIED and EVENTS[event.id].get(
                "proxied_node"
            ):
                slug = EVENTS[event.id]["proxied_node"]

        node = slug[0].upper() + slug[1:]
        return node

    async def get_event_enabled_state(self, event: EventInfo) -> bool:
        """Get event detection state."""
        state = await self.request(GET, event.url)
        node = self.get_event_state_node(event)
        return (
            str_to_bool(state[node].get("enabled", "false")) if state.get(node) else False
        )

    async def get_event_switch_mutex(self, event: EventInfo, channel_id: int) -> list[MutexIssue]:
        """Get if event is mutually exclusive with enabled events."""
        mutex_issues = []

        if not EVENTS[event.id].get("mutex"):
            return mutex_issues

        # Use alt event ID for mutex due to API inconsistencies
        event_id = event.id
        if MUTEX_ALTERNATE_IDS.get(event.id):
            event_id = MUTEX_ALTERNATE_IDS[event.id]

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

    async def set_event_enabled_state(
        self, channel_id: int, event: EventInfo, is_enabled: bool
    ) -> None:
        """Set event detection state."""
        _LOGGER.debug(
            "Setting enabled state for event: %s on channel: %d to %s",
            event.id,
            channel_id,
            is_enabled,
        )

        # Validate that this event switch is not mutually exclusive with another enabled one
        mutex_issues = []
        if (
            channel_id != 0
            and is_enabled
            and self.device_info.support_event_mutex_checking
        ):
            mutex_issues = await self.get_event_switch_mutex(event, channel_id)

        if not mutex_issues:
            data = await self.request(GET, event.url)
            node = self.get_event_state_node(event)
            new_state = bool_to_str(is_enabled)
            if new_state == data[node]["enabled"]:
                return
            data[node]["enabled"] = new_state
            xml = xmltodict.unparse(data)
            await self.request(PUT, event.url, present="xml", data=xml)
        else:
            error_message = (
                f"You cannot enable {EVENTS[event.id]['label']} events. "
                f"Please disable {EVENTS[mutex_issues[0].event_id]['label']} "
                f"on channels {mutex_issues[0].channels} first"
            )
            _LOGGER.error(error_message)
            raise HomeAssistantError(error_message)

    async def get_port_status(self, port_type: str, port_no: int) -> str:
        """Get status of physical ports."""
        if port_type == "input":
            status = await self.request(GET, f"System/IO/inputs/{port_no}/status")
        else:
            status = await self.request(GET, f"System/IO/outputs/{port_no}/status")
        return deep_get(status, "IOPortStatus.ioState")

    async def set_port_state(self, port_no: int, turn_on: bool):
        """Set status of output port."""
        data = {}
        if turn_on:
            data["IOPortData"] = {"outputState": "high"}
        else:
            data["IOPortData"] = {"outputState": "low"}

        xml = xmltodict.unparse(data)
        await self.request(
            PUT, f"System/IO/outputs/{port_no}/trigger", present="xml", data=xml
        )

    async def get_holiday_enabled_state(self, holiday_index=0) -> bool:
        """Get holiday state."""
        data = await self.request(GET, "System/Holidays")
        holiday = data["HolidayList"]["holiday"][holiday_index]
        return str_to_bool(holiday["enabled"]["#text"])

    async def set_holiday_enabled_state(
        self, is_enabled: bool, holiday_index=0
    ) -> None:
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
        """Extract event notification host from data."""
        hosts = deep_get(data, "HttpHostNotificationList.HttpHostNotification", {})
        if isinstance(hosts, list):
            return hosts[0]
        return hosts

    async def get_alarm_server(self) -> AlarmServer | None:
        """Get event notifications listener server URL."""
        data = await self.request(GET, "Event/notification/httpHosts")
        if not data:
            return None
        host = self._get_event_notification_host(data)

        return AlarmServer(
            ipAddress=host.get("ipAddress"),
            portNo=int(host.get("portNo")),
            url=host.get("url"),
            protocolType=host.get("protocolType"),
        )

    async def set_alarm_server(self, base_url: str, path: str) -> None:
        """Set event notifications listener server."""
        address = urlparse(base_url)
        data = await self.request(GET, "Event/notification/httpHosts")
        if not data:
            return
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
        await self.request(
            PUT, "Event/notification/httpHosts", present="xml", data=xml
        )

    async def reboot(self):
        """Reboot device."""
        await self.request(PUT, "System/reboot", present="xml")

    async def request(
        self,
        method: str,
        url: str,
        present: str = "dict",
        **data,
    ) -> Any:
        """Send request and log response, returns {} if request fails."""
        full_url = self.isapi.get_url(url)
        _LOGGER.debug(
            "Sending %s request to URL: %s with data: %s", method, full_url, data
        )
        try:
            response = await self.isapi.request(method, full_url, present, **data)
            _LOGGER.debug(
                "Received response for %s %s: %s", method, full_url, response
            )
            if data:
                _LOGGER.debug("Payload sent: %s", data)
        except HTTPStatusError as ex:
            _LOGGER.error("HTTPStatusError for %s %s: %s", method, full_url, ex)
            if self.pending_initialization:
                _LOGGER.warning("Suppressing HTTP error during initialization")
                return {}
            raise
        except Exception as ex:
            _LOGGER.exception(
                "Unexpected exception during %s request to %s: %s", method, full_url, ex
            )
            raise
        else:
            return response

    def handle_exception(self, ex: Exception, details: str = "") -> bool:
        """Handle common exception, returns False if exception remains unhandled."""

        def is_reauth_needed():
            if isinstance(ex, HTTPStatusError):
                status_code = ex.response.status_code
                if status_code in (HTTPStatus.UNAUTHORIZED,):
                    return True
            return False

        host = self.isapi.host
        if is_reauth_needed():
            # Re-establish session
            self.isapi = ISAPI_Client(
                host,
                self.isapi.username,
                self.isapi.password,
                self.isapi.session,
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

        detection_target = deep_get(alert, "DetectionRegionList.DetectionRegionEntry.detectionTarget")
        region_id = int(deep_get(alert, "DetectionRegionList.DetectionRegionEntry.regionID", 0))

        if not EVENTS.get(event_id):
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
            full_url = self.isapi.get_url(url)
            chunks = self.isapi.request_bytes(GET, full_url, params=params)
        else:
            url = f"Streaming/channels/{stream.id}/picture"
            full_url = self.isapi.get_url(url)
            chunks = self.isapi.request_bytes(GET, full_url, params=params)
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
        u = quote(self.isapi.username, safe="")
        p = quote(self.isapi.password, safe="")
        url = f"{self.device_info.ip_address}:{self.device_info.rtsp_port}/Streaming/channels/{stream.id}"
        return f"rtsp://{u}:{p}@{url}"


def str_to_bool(value: str) -> bool:
    """Convert text to boolean."""
    result = value.lower() == "true" if value else False
    return result


def bool_to_str(value: bool) -> str:
    """Convert boolean to 'true' or 'false'."""
    return "true" if value else "false"


def get_stream_id(channel_id: str, stream_type: int = 1) -> int:
    """Get stream id."""
    return int(channel_id) * 100 + stream_type


def deep_get(dictionary: dict, path: str, default: Any = None) -> Any:
    """Get safely nested dictionary attribute."""
    result = reduce(
        lambda d, key: d.get(key, default) if isinstance(d, dict) else default,
        path.split("."),
        dictionary,
    )
    return result
