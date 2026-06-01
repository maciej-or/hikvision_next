"""Hikvision ISAPI client."""

from __future__ import annotations

import traceback
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
import time

from homeassistant.const import (
    STATE_ON, STATE_OFF
)
from .const import (
    CONNECTION_TYPE_DIRECT,
    CONNECTION_TYPE_PROXIED,
    EVENT_BASIC,
    EVENT_IO,
    EVENT_PIR,
    EVENT_TRAFFIC,
    EVENTS,
    EVENTS_ALTERNATE_ID,
    GET,
    MUTEX_ALTERNATE_ID,
    POST,
    PUT,
    STREAM_TYPE,
    SUBSCRIBE_ENDPOINT,
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
import hashlib


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
        self._ext_session = None
        self._isLogin: bool = False
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

        self.capabilities.support_storage = str_to_bool(deep_get(capabilities, "SysCap.isSupportStorageExtraInfo", "false"))

        itc_capability = deep_get(capabilities, "ITCCap", {})
        self.capabilities.support_anpr = str_to_bool(deep_get(itc_capability, "isSupportVehicleDetection", "false"))

        # Set if NVR based on whether more than 1 supported IP or analog cameras
        # Single IP camera will show 0 supported devices in total
        if self.capabilities.analog_cameras_inputs + self.capabilities.digital_cameras_inputs > 1:
            self.device_info.is_nvr = True

        await self.get_cameras()

        self.supported_events = await self.get_supported_events(capabilities)

        await self.get_protocols()

        with suppress(Exception):
            if self.capabilities.support_storage:
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

        if self.device_info.is_nvr:
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

        # if self.capabilities.support_anpr and not self.device_info.is_nvr:
        #     # TODO: add support for NVR
        #     event_trigger = await self.request(GET, "Event/triggers/vehicledetection-1")
        #     event_trigger = deep_get(event_trigger, "EventTrigger", {})
        #     if event := create_event_info(event_trigger):
        #         events.append(event)

        # Fetch the dedicated and more complete SubscribeEvent capability
        # This is the correct source for building generic multi-event subscriptions.
        subscribe_event_cap_raw = await self.request(GET, "Event/notification/subscribeEventCap")
        subscribe_cap = subscribe_event_cap_raw.get("SubscribeEventCap", subscribe_event_cap_raw) or {}

        cap_events = deep_get(subscribe_cap, "EventList.Event", [])

        # Also fetch the original httpHosts/capabilities (still useful for some flags like ANPR)
        # events_capabilities = await self.request(GET, "Event/notification/httpHosts/capabilities")
        # anpr_events = deep_get(events_capabilities, "HttpHostNotificationCap.ANPR", [])

        # Rich parsing using the proper SubscribeEventCap from the dedicated endpoint
        self._parse_subscribe_event_cap(subscribe_cap, cap_events)

        # Legacy simple flag
        self.capabilities.support_subscribe_event = (
            str_to_bool(deep_get(subscribe_cap, "isSupportSubscribeEvent", "false"))
            or bool(cap_events)
            or self.capabilities.subscribe_event_cap.supports_subscribe
        )

        # Use the parsed SubscribeEventCap data (more accurate and consistent)
        cap_info = self.capabilities.subscribe_event_cap

        if "AccessControllerEvent" in cap_info.supported_event_types:
            events.append(EventInfo(
                id="door",
                channel_id=0,
                io_port_id=0,
                notifications=["center"]
            ))
            events.append(EventInfo(
                id="lock",
                channel_id=0,
                io_port_id=0,
                url="AccessControl/RemoteControl/door/1",
                notifications=["center"]
            ))
            events.append(EventInfo(
                id="face",
                channel_id=0,
                io_port_id=0,
                notifications=["center"]
            ))

        # ANPR: prefer the parsed supported_event_types, fallback to previous logic
        if (
            "ANPR" in cap_info.supported_event_types
            or self.capabilities.support_anpr
        ):
            events.append(EventInfo(
                id="anpr",
                channel_id=0,
                io_port_id=0,
                notifications=["center"]
            ))

        return events

    def _parse_subscribe_event_cap(self, subscribe_cap: dict, cap_events: list) -> None:
        """Parse SubscribeEventCap (from /Event/notification/subscribeEventCap) into rich info."""
        cap_info = self.capabilities.subscribe_event_cap

        if not subscribe_cap:
            return

        cap_info.raw_caps = subscribe_cap
        cap_info.supports_subscribe = True

        # Formats
        fmt = deep_get(subscribe_cap, "format", {})
        if isinstance(fmt, dict):
            cap_info.formats = fmt.get("@opt", "").split(",")
        else:
            cap_info.formats = [fmt] if fmt else ["xml"]

        # Channel / Event modes
        cap_info.channel_modes = deep_get(subscribe_cap, "channelMode", {}).get("@opt", "").split(",")
        cap_info.event_modes = deep_get(subscribe_cap, "eventMode", {}).get("@opt", "").split(",")

        # Global picture types
        pic = deep_get(subscribe_cap, "pictureURLType", {})
        if isinstance(pic, dict):
            cap_info.picture_url_types = pic.get("@opt", "").split(",")
            # cap_info.default_picture_url_type = pic.get("@def", "")
            cap_info.default_picture_url_type = "cloudStorageURL" if "cloudStorageURL" in cap_info.picture_url_types else pic.get("@def", "")
        else:
            cap_info.picture_url_types = [pic] if pic else []

        # Per-event information
        if not isinstance(cap_events, list):
            cap_events = [cap_events] if cap_events else []

        supported_types = []
        for evt in cap_events:
            if not isinstance(evt, dict):
                continue
            etype = evt.get("type")
            if not etype:
                continue
            supported_types.append(etype)

            pic_info = evt.get("pictureURLType", {})
            if isinstance(pic_info, dict):
                allowed = pic_info.get("@opt", "").split(",")
                cap_info.event_picture_types[etype] = [p for p in allowed if p]
            else:
                cap_info.event_picture_types[etype] = [pic_info] if pic_info else []

        cap_info.supported_event_types = supported_types

        # Heuristic: if device only returns specific events in EventList, it likely requires explicit list
        if cap_info.event_modes and "list" in cap_info.event_modes and "all" not in cap_info.event_modes:
            cap_info.requires_event_list = True
        if cap_events:
            cap_info.requires_event_list = True

        # Note: Actual EventInfo registration for door/lock/face/anpr continues
        # in the main body of get_supported_events after this call.

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
        elif event_type == EVENT_TRAFFIC:
            # /ISAPI/Traffic/channels/1/vehicleDetect
            url = f"Traffic/channels/{channel_id}/{slug}"
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

        if event.id == "lock":
            return None

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

        if event.id == "lock":
            data = {
                "RemoteControlDoor": {
                    '@xmlns': 'http://www.isapi.org/ver20/XMLSchema',
                    '@version': '2.0',
                    "cmd": "open" if is_enabled else "close"
                }
            }
            xml = xmltodict.unparse(data)
            _LOGGER.info(f"Set lock state {event.url} -> {data['RemoteControlDoor']['cmd']}")
            await self.ext_request(PUT, event.url, present="xml", data=xml)
            return None

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
            return hosts

    async def get_alarm_server(self) -> AlarmServer | None:
        """Get event notifications listener server URL."""

        data = await self.request(GET, "Event/notification/httpHosts")
        if not data:
            return None
        hosts = self._get_event_notification_host(data)
        for host in hosts:
            if host.get("protocolType", "").lower() != "http":
                continue

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
        hosts = self._get_event_notification_host(data)

        for host in hosts:
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

            if host["protocolType"].upper() not in ("HTTP", "HTTPS"):
                continue

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
    def parse_event_notification(xml: str | dict) -> AlertInfo | None:
        """Parse incoming EventNotificationAlert XML message.

        This method is now more defensive because subscribeEvent streams
        (especially heartbeats) can sometimes deliver partial or concatenated data.
        """
        if isinstance(xml, dict):
            alert = xml
        else:
            try:
                # Fix for some cameras sending non html encoded data
                xml = xml.replace("&", "&amp;")
                data = xmltodict.parse(xml)
                alert = data.get("EventNotificationAlert")
                if alert is None:
                    return None
            except Exception as e:
                # Gracefully ignore malformed / heartbeat / junk data from streams
                # (very common with heartBeat events over subscribeEvent)
                _LOGGER.warning("Failed to decode EventNotificationAlert", exc_info=e)
                return None

        event_id = alert.get("eventType")
        if not event_id or event_id == "duration":
            # <EventNotificationAlert version="2.0"
            try:
                event_id = alert["DurationList"]["Duration"]["relationEvent"]
            except Exception:
                return None

        event_id = str(event_id).lower()

        # Handle both "heartbeat" and "heartBeat" (device sends camelCase)
        if event_id in ("heartbeat", "heartbeat"):
            return None

        if event_id == "accesscontrollerevent":
            ace = alert.get("AccessControllerEvent")
            if ace.get("majorEventType") == 5:
                if ace.get("subEventType") in (0x15, 0x16, 0x13, 0x14):
                    return AlertInfo(
                        0,
                        0,
                        "lock",
                        None,
                        alert.get("macAddress"),
                        None,
                        None,
                        STATE_ON if ace.get("subEventType") in (0x15, 0x13) else STATE_OFF
                    )
                elif ace.get("subEventType") in (0x19, 0x1a):
                    # MINOR_DOOR_OPEN_NORMAL = 0x19
                    # MINOR_DOOR_CLOSE_NORMAL = 0x1a
                    return AlertInfo(
                        0,
                        0,
                        "door",
                        None,
                        alert.get("macAddress"),
                        None,
                        None,
                        STATE_ON if ace.get("subEventType") == 0x19 else STATE_OFF
                    )
                elif ace.get("subEventType") == 0x4b:
                    # MINOR_FACE_VERIFY_PASS 人脸认证通过
                    return AlertInfo(
                        0,
                        0,
                        "face",
                        None,
                        alert.get("macAddress"),
                        None,
                        None,
                        ace.get("employeeNoString") + ": " + ace.get("name")
                    )

            return None
        else:
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
            full_url = self.get_isapi_url(url)
            chunks = self.request_bytes(GET, full_url, params=params)
        else:
            url = f"Streaming/channels/{stream.id}/picture"
            full_url = self.get_isapi_url(url)
            chunks = self.request_bytes(GET, full_url, params=params)
        data = b"".join([chunk async for chunk in chunks])

        if data.startswith(b"<?xml "):
            error = xmltodict.parse(data)
            if error is None:
                return None
            if str_status_code := deep_get(error, "ResponseStatus.statusCode"):
                status_code = int(str_status_code)
            else:
                return None
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

        if not self._auth_method and response.status_code != 200:
            _LOGGER.error("Authentication method not detected, %s", response.status_code)
            self._session = None
            if response.headers:
                _LOGGER.error("response.headers %s", response.headers)

    async def _session_login(self):
        if self._isLogin:
            return
        # Step 1: 获取登录能力（capabilities）
        url = "Security/sessionLogin/capabilities"
        params = {'username': self.username}

        if not self._ext_session:
            self._ext_session = httpx.AsyncClient(timeout=self.timeout, verify=self.verify_ssl)

        resp = await self._ext_session.get(self.get_isapi_url(url), params=params)
        resp.raise_for_status()

        # 使用 xmltodict 解析 XML
        data = xmltodict.parse(resp.text)

        # 提取需要的信息
        session_info = data.get('SessionLoginCap', {}) or data.get('sessionLoginCap', {})

        salt = session_info.get('salt')
        challenge = session_info.get('challenge')
        iterations = int(session_info.get('iterations', 0))
        session_id = session_info.get('sessionID')
        session_id_version = session_info.get('sessionIDVersion')

        if not all([salt, challenge, iterations, session_id, session_id_version]):
            _LOGGER.error("❌ capabilities return data invalid")
            raise HTTPStatusError("capabilities return data invalid")

        try:
            # 第一次哈希
            hash_pwd = hashlib.sha256(f"{self.username}{salt}{self.password}".encode('utf-8')).hexdigest()

            # 第二次哈希（加 challenge）
            hash_pwd = hashlib.sha256(f"{hash_pwd}{challenge}".encode('utf-8')).hexdigest()

            # 后续迭代
            for _ in range(2, iterations):
                hash_pwd = hashlib.sha256(hash_pwd.encode('utf-8')).hexdigest()

            # Step 3: 提交登录
            login_url = f"Security/sessionLogin?timeStamp={int(time.time() * 1000)}"

            login_xml = f"""<SessionLogin>
    <userName>{self.username}</userName>
    <password>{hash_pwd}</password>
    <sessionID>{session_id}</sessionID>
    <sessionIDVersion>{session_id_version}</sessionIDVersion>
</SessionLogin>"""

            headers = {
                'Content-Type': 'application/xml; charset=utf-8'
            }

            resp = await self._ext_session.post(self.get_isapi_url(login_url), content=login_xml, headers=headers)

            # 解析返回结果
            result = xmltodict.parse(resp.text)
            status_value = result.get('SessionLogin', {}).get('statusValue')

            if status_value == '200' or status_value == 200:
                self._isLogin = True
                _LOGGER.info("✅ 海康威视异步登录成功")
                return True
            else:
                _LOGGER.error(f"❌ 登录失败，statusValue: {status_value}")
                return False

        except Exception as e:
            _LOGGER.error(f"❌ 登录过程出错: {e}")
            return False

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
        response = None
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
            if response.status_code == 401:
                self._auth_method = None

            response.raise_for_status()
            result = parse_isapi_response(response, present)
            _LOGGER.debug("--- [%s] %s", method, full_url)
            if data:
                _LOGGER.debug(">>> payload:\n%s", data)
            _LOGGER.debug("\n%s", result)
        except HTTPStatusError as ex:
            _LOGGER.info("--- [%s] %s\n%s", method, full_url, ex)
            if response is not None and response.status_code not in (200, 404):
                _LOGGER.info("--- [%s] %d Error: %s", method, response.status_code, response.text)
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

    async def ext_request(
            self,
            method: str,
            url: str,
            present: str = "dict",
            data: str = None,
    ) -> Any:
        """Send ISAPI request and log response, returns {} if request fails."""
        full_url = self.get_isapi_url(url)
        response = None
        try:
            if not self._isLogin:
                await self._session_login()

            response = await self._ext_session.request(
                method,
                full_url,
                auth=self._auth_method,
                data=data,
                timeout=self.timeout,
            )
            if response.status_code == 401:
                self._isLogin = False

            response.raise_for_status()
            result = parse_isapi_response(response, present)
            _LOGGER.debug("--- [%s] %s", method, full_url)
            if data:
                _LOGGER.debug(">>> payload:\n%s", data)
            _LOGGER.debug("\n%s", result)
        except HTTPStatusError as ex:
            _LOGGER.info("--- [%s] %s\n%s", method, full_url, ex)
            if response is not None and response.status_code not in (200, 404):
                _LOGGER.info("--- [%s] %d Error: %s", method, response.status_code, response.text)
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

    async def subscribe_events(self, xml: str) -> httpx.Response:
        """Send subscribeEvent request and return the streaming response.

        The caller is responsible for reading the stream and closing the response.
        This is used by EventSubscription for long-lived event push channels
        (e.g. ANPR) that are not delivered via the normal alarm server callback.
        """
        full_url = self.get_isapi_url(SUBSCRIBE_ENDPOINT)

        if not self._auth_method:
            await self._detect_auth_method()

        request = self._session.build_request(
            "POST",
            full_url,
            content=xml,
            headers={"Content-Type": "application/xml"},
            timeout=300
        )
        response = await self._session.send(request, auth=self._auth_method, stream=True)

        if response.status_code == 401:
            self._isLogin = False
            self._auth_method = None

        if response.status_code not in (200, 404):
            # Must consume the body first on streaming responses
            body = await response.aread()
            _LOGGER.warning("--- Error %d on subscribeEvent: %s", response.status_code, body)
            # Re-raise after consuming
            response.raise_for_status()
        else:
            response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        _LOGGER.debug(
            "--- [POST] subscribeEvent long-lived connection established, content-type=%s",
            content_type,
        )
        return response

    def build_subscribe_event_xml(
        self,
        event_types: list[str] | None = None,
        channel_mode: str | None = None,
        event_mode: str | None = None,
        picture_url_type: str | None = None,
        heartbeat: int = 5,
        level: str = "high",
    ) -> str:
        """Build a device-compatible <SubscribeEvent> XML payload.

        This makes EventSubscription a generic multi-event subscriber.
        """
        cap = self.capabilities.subscribe_event_cap

        # Decide modes
        ch_mode = channel_mode or ("all" if "all" in cap.channel_modes else "list")
        ev_mode = event_mode or ("all" if "all" in cap.event_modes else "list")

        # Decide picture type
        if not picture_url_type:
            if cap.default_picture_url_type:
                picture_url_type = cap.default_picture_url_type
            elif cap.picture_url_types:
                picture_url_type = cap.picture_url_types[0]
            else:
                picture_url_type = "binary"

        data = {
            "SubscribeEvent": {
                "heartbeat": heartbeat,
                "channelMode": ch_mode,
                "eventMode": ev_mode,
            }
        }

        # Build EventList when required or when specific events are requested
        need_event_list = (
            cap.requires_event_list
            or ev_mode == "list"
            or (event_types and len(event_types) > 0)
        )

        if need_event_list:
            data['SubscribeEvent']['EventList'] = {
                'Event': []
            }

            targets = event_types or cap.supported_event_types or ["ANPR"]

            for etype in targets:
                sub_data = {
                    "type": etype
                }

                # Per-event picture type if available
                allowed = cap.event_picture_types.get(etype, cap.picture_url_types)
                if allowed:
                    chosen = picture_url_type if picture_url_type in allowed else allowed[0]
                    sub_data['pictureURLType'] = chosen

                try:
                    raw_evtList = cap.raw_caps.get('EventList')
                    if raw_evtList:
                        raw_evtList = raw_evtList.get('Event')
                        if not isinstance(raw_evtList, list):
                            raw_evtList = [raw_evtList]
                        for evt in raw_evtList:
                            if evt.get('type') == etype:
                                for ev_k, ev_v in evt.items():
                                    if ev_k in ['type', 'pictureURLType']:
                                        continue
                                    if ev_k in ['minorEvent', 'minorAlarm', 'minorException', 'minorOperation']:
                                        ev_v = ev_v.split(",")
                                        if ev_k == 'minorEvent':
                                            ev_v = [x for x in ev_v if x not in ('0x51','0x52', '0x813')]
                                        if ev_k == 'minorOperation':
                                            # Remove For 远程手动校时, NTP自动校时, 远程实时布防, 远程实时撤防
                                            ev_v = [x for x in ev_v if x not in ('0x404', '0x405', '0x419', '0x41a')]
                                        ev_v = ",".join(ev_v)
                                    sub_data[ev_k] = ev_v
                except Exception as e:
                    traceback.print_exception(e)

                data['SubscribeEvent']['EventList']['Event'].append(sub_data)

        # Global picture preference
        data['SubscribeEvent']['pictureURLType'] = picture_url_type
        data['SubscribeEvent']['level'] = level
        # data['SubscribeEvent']['SubscribeISAPIMessage'] = {
        #     'EventTypeList': {
        #         'eventType': 'all',
        #         'uploadPicEnable': 'true'
        #     },
        #     'EventList': {
        #         'Event': {
        #             'eventType': 'all',
        #             'uploadPicEnable': 'true'
        #         }
        #     }
        # }
        data['SubscribeEvent']['eventAck'] = 'false'
        data['SubscribeEvent']['changedUploadSub'] = None

        xml = xmltodict.unparse(data)

        _LOGGER.info("SubscribeEvent XML -> %s", xml)
        return xml


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
