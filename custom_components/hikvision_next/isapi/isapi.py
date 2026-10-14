"""Hikvision ISAPI client."""

from __future__ import annotations

import asyncio
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
    EVENT_THERMAL,
    EVENTS,
    EVENTS_ALTERNATE_ID,
    GET,
    MUTEX_ALTERNATE_ID,
    PARTITION_ARM_AWAY,
    PERIPHERAL_ENDPOINTS,
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
    Partition,
    Peripheral,
    ProtocolsInfo,
    SecurityHostStatus,
    StorageInfo,
    Zone,
)
from .multipart_stream import MultipartStreamParser, extract_boundary
from .session_auth import SessionLoginAuth
from .utils import bool_to_str, deep_get, json_bool, parse_isapi_response, str_to_bool

# Helper to sanitize channel IDs (e.g. converting "I-1" to 1)
def clean_int(value):
    """Sanitize and convert value to int, handling alphanumeric strings."""
    try:
        return int(value)
    except (ValueError, TypeError):
        # Extract digits from string (e.g. "I-1" -> "1")
        return int("".join(filter(str.isdigit, str(value))))

Node = dict[str, Any]

# How long to wait for a single chunk (heartbeat or event) on the arming connection before
# considering it stale and reconnecting.
ARMING_IDLE_TIMEOUT = 90

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
        self.partitions: list[Partition] = []
        self.zones: list[Zone] = []
        self.peripherals: list[Peripheral] = []
        self.pending_initialization = False
        self._forbidden_cache: set[str] = set()

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

        security_cp_cap = (await self._security_cp_request(GET, "SecurityCP/capabilities")).get("SecurityCPCap")
        if security_cp_cap:
            self.device_info.is_security_panel = True
            self.capabilities.partitions = int(security_cp_cap.get("partitionNum", 0))
            self.capabilities.zones = int(security_cp_cap.get("localZoneNum", 0)) + int(
                security_cp_cap.get("extendZoneNum", 0)
            ) + int(security_cp_cap.get("wirelessZoneNum", 0))

            self.partitions = await self.get_partitions()
            self.zones = await self.get_zones()
            # Peripherals are an enhancement on top of the core partition/zone support, gated
            # behind a status/capabilities endpoint that may simply not exist on older
            # firmware -- don't fail the whole device setup over it.
            with suppress(Exception):
                self.peripherals = await self.get_peripherals()
            return

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
                        serial_no = f"{self.device_info.serial_no}_{source.get('proxyProtocol')}_{camera_id}"

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
                    camera_id = clean_int(analog_camera.get("id"))
                    device_serial_no = f"{self.device_info.serial_no}-VI{camera_id}"
                    input_port = clean_int(analog_camera.get("inputPort"))

                    self.cameras.append(
                        AnalogCamera(
                            id=camera_id,
                            name=analog_camera.get("name"),
                            model=analog_camera.get("resDesc"),
                            serial_no=device_serial_no,
                            input_port=input_port,
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

            raw_event_types = event_type if isinstance(event_type, list) else [event_type]
            event_types: list[str] = []
            for raw_event_type in raw_event_types:
                if isinstance(raw_event_type, dict):
                    raw_event_type = raw_event_type.get("#text")
                if raw_event_type:
                    event_types.append(str(raw_event_type))

            if not event_types:
                return None

            event_id = None
            for candidate in event_types:
                if candidate in EVENTS_ALTERNATE_ID:
                    normalized = EVENTS_ALTERNATE_ID[candidate]
                else:
                    normalized = candidate.lower()
                    if normalized not in EVENTS and "-" in normalized:
                        prefix = normalized.split("-", 1)[0]
                        if prefix in EVENTS_ALTERNATE_ID:
                            normalized = EVENTS_ALTERNATE_ID[prefix]
                        elif prefix in EVENTS:
                            normalized = prefix
                    if normalized in EVENTS_ALTERNATE_ID:
                        normalized = EVENTS_ALTERNATE_ID[normalized]

                if normalized in EVENTS:
                    event_id = normalized
                    break

            if event_id is None:
                event_id = event_types[0].lower()
                if event_id not in EVENTS and "-" in event_id:
                    prefix = event_id.split("-", 1)[0]
                    if prefix in EVENTS_ALTERNATE_ID:
                        event_id = EVENTS_ALTERNATE_ID[prefix]
                    elif prefix in EVENTS:
                        event_id = prefix
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
                io_port = clean_int(event_trigger.get("inputIOPortID", 0))
                if not io_port:
                    io_port = clean_int(event_trigger.get("dynInputIOPortID", 0))
                    is_proxy = io_port > 0
            else:
                channel_id = clean_int(event_trigger.get("videoInputChannelID", 0))
                if not channel_id:
                    channel_id = clean_int(event_trigger.get("dynVideoInputChannelID", 0))
                    is_proxy = channel_id > 0
                # Fallback: Extract channel from event ID (e.g., "VMD-1" → 1)
                # Some firmware versions don't include videoInputChannelID for all events
                if not channel_id:
                    event_id_raw = event_trigger.get("id")
                    if isinstance(event_id_raw, list):
                        event_id_raw = event_id_raw[0] if event_id_raw else None
                    if event_id_raw and "-" in str(event_id_raw):
                        with suppress(ValueError, IndexError):
                            channel_id = int(str(event_id_raw).rsplit("-", 1)[1])

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

        # Build mapping of eventType → original ID prefix for URL construction
        # e.g., "vmd" → "VMD", "tamperdetection" → "tamper", "thermometry" → "thermometry"
        # This is needed because ChannelEventCapList uses different names than Event/triggers IDs
        event_url_prefix_map = {}
        for event_trigger in available_events:
            event_type = event_trigger.get("eventType")
            if isinstance(event_type, list):
                event_type = event_type[0] if event_type else None
            if isinstance(event_type, dict):
                event_type = event_type.get("#text")
            event_id_raw = event_trigger.get("id")
            if isinstance(event_id_raw, list):
                event_id_raw = event_id_raw[0] if event_id_raw else None
            if isinstance(event_id_raw, dict):
                event_id_raw = event_id_raw.get("#text")
            if event_type and event_id_raw:
                event_type_key = str(event_type)
                event_id_key = str(event_id_raw)
                # Extract prefix (e.g., "VMD-1" → "VMD", "thermometry-2" → "thermometry")
                prefix = event_id_key.rsplit("-", 1)[0] if "-" in event_id_key else event_id_key
                event_type_lower = event_type_key.lower()
                event_url_prefix_map[event_type_lower] = prefix
                # Also map the translated event ID (e.g., "vmd" → "motiondetection")
                # so that "motiondetection" from ChannelEventCapList can find "VMD" prefix
                translated_id = EVENTS_ALTERNATE_ID.get(event_type_lower) or EVENTS_ALTERNATE_ID.get(event_type_key)
                if translated_id and translated_id not in event_url_prefix_map:
                    event_url_prefix_map[translated_id] = prefix

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
        if self.capabilities.is_multi_channel or not event_triggers:
            channels_capabilities = await self.request(GET, "Event/channels/capabilities")
            channel_events = deep_get(channels_capabilities, "ChannelEventCapList.ChannelEventCap", [])
            for event_cap in channel_events:
                event_types = deep_get(event_cap, "eventType").get("@opt", "").split(",")
                channel_id = clean_int(event_cap.get("channelID"))
                for event_type in event_types:
                    original_id = event_type.lower()
                    event_id = original_id
                    if event_id in EVENTS_ALTERNATE_ID:
                        event_id = EVENTS_ALTERNATE_ID[event_id]
                    if event_id not in EVENTS:
                        continue
                    if event_id == EVENT_IO:
                        continue
                    if not [e for e in events if (e.id == event_id and e.channel_id == channel_id)]:
                        # Use the original ID prefix from Event/triggers response if available
                        # e.g., ChannelEventCapList has "motionDetection" but camera expects "VMD-1"
                        # Try both the original_id and the translated event_id for lookup
                        url_prefix = event_url_prefix_map.get(original_id) or event_url_prefix_map.get(event_id, original_id)
                        event_trigger = await self.request(GET, f"Event/triggers/{url_prefix}-{channel_id}")
                        event_trigger = deep_get(event_trigger, "EventTrigger", {})
                        if event := create_event_info(event_trigger):
                            events.append(event)
                        elif event_id in EVENTS:
                            # Fallback: Create event from ChannelEventCapList even if Event/triggers fails
                            # Some cameras report capabilities but return 403 for Event/triggers/{event}-{channel}
                            # Yet they still send notifications for these events
                            url = self.get_event_url(event_id, channel_id, 0, False)
                            events.append(
                                EventInfo(
                                    channel_id=channel_id,
                                    io_port_id=0,
                                    id=event_id,
                                    url=url,
                                    is_proxy=False,
                                    notifications=["center"],
                                )
                            )

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
        elif event_type == EVENT_THERMAL:
            # ISAPI/Thermal/channels/{channel_id}/thermometry/basicParam
            url = f"Thermal/channels/{channel_id}/{slug}"
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

    # --- Security control panel (SecurityCP): partitions (areas) and zones ---
    # Unlike the rest of ISAPI, the SecurityCP namespace on AX Hybrid/Hybrid PRO panels
    # only returns JSON: it does not have a usable default XML representation, so all
    # SecurityCP requests explicitly ask for format=json and parse the body as JSON.

    async def _security_cp_request(self, method: str, url: str, query: str = "", data: str | None = None) -> dict:
        """Send a request to a SecurityCP/* endpoint and parse its JSON response."""
        separator = "&" if "?" in url else "?"
        extra = f"&{query}" if query else ""
        full_url = f"{url}{separator}format=json{extra}"
        # Only reads may be short-circuited by the forbidden cache: a refused arm/bypass (403) must not
        # block every later attempt, matching how all other write requests opt out.
        response = await self.request(method, full_url, present="json", data=data, use_forbidden_cache=method == GET)
        return json.loads(response) if response else {}

    @staticmethod
    def _security_cp_list(data: dict) -> list[dict]:
        """Extract the list of items from a SecurityCP JSON list response.

        Different endpoints wrap the list under a generic "List" key or an
        endpoint-specific one (e.g. "SubSysList", "ZoneList").
        """
        for value in data.values():
            if isinstance(value, list):
                return value
        return []

    async def get_partitions(self) -> list[Partition]:
        """Get security control panel partitions (areas), combining configuration and status."""
        config_data = await self._security_cp_request(GET, "SecurityCP/Configuration/subSys")
        status_by_id = {p.id: p for p in await self.get_partitions_status()}

        partitions = []
        for wrapper in self._security_cp_list(config_data):
            item = wrapper.get("SubSys", {})
            partition_id = int(item.get("id"))
            enabled = json_bool(item.get("enabled", True))
            if not enabled:
                # The panel always reports all possible partition slots (e.g. 16), regardless
                # of how many are actually configured/in use -- skip the disabled ones rather
                # than creating an alarm_control_panel entity for every unused slot.
                continue

            status_item = status_by_id.get(partition_id)
            partitions.append(
                Partition(
                    id=partition_id,
                    name=item.get("name") or f"Partition {partition_id}",
                    enabled=enabled,
                    arming=status_item.arming if status_item else "disarm",
                    alarm=status_item.alarm if status_item else False,
                    delay_time=status_item.delay_time if status_item else 0,
                )
            )
        return partitions

    async def get_partitions_status(self) -> list[Partition]:
        """Get current arming/alarm status of all partitions (areas)."""
        data = await self._security_cp_request(GET, "SecurityCP/status/subSystems")
        partitions = []
        for wrapper in self._security_cp_list(data):
            item = wrapper.get("SubSys", {})
            partitions.append(
                Partition(
                    id=int(item.get("id")),
                    name=item.get("name") or f"Partition {item.get('id')}",
                    enabled=json_bool(item.get("enabled", True)),
                    arming=item.get("arming", "disarm"),
                    alarm=json_bool(item.get("alarm", False)),
                    delay_time=int(item.get("delayTime", 0)),
                )
            )
        return partitions

    def get_partition_by_id(self, partition_id: int) -> Partition | None:
        """Get partition object by id."""
        for partition in self.partitions:
            if partition.id == partition_id:
                return partition
        return None

    async def arm_partition(self, partition_id: int, mode: str = PARTITION_ARM_AWAY) -> None:
        """Arm a partition (area). mode is 'stay' or 'away'."""
        await self._security_cp_request(PUT, f"SecurityCP/control/arm/{partition_id}", f"ways={mode}")

    async def disarm_partition(self, partition_id: int) -> None:
        """Disarm a partition (area)."""
        await self._security_cp_request(PUT, f"SecurityCP/control/disarm/{partition_id}")

    async def clear_partition_alarm(self, partition_id: int) -> None:
        """Clear a triggered alarm for a partition (area)."""
        await self._security_cp_request(PUT, f"SecurityCP/control/clearAlarm/{partition_id}")

    async def get_zones(self) -> list[Zone]:
        """Get security control panel zones, combining configuration and status."""
        config_data = await self._security_cp_request(GET, "SecurityCP/Configuration/zones")
        status_by_id = {z.id: z for z in await self.get_zones_status()}

        zones = []
        for wrapper in self._security_cp_list(config_data):
            item = wrapper.get("Zone", {})
            zone_id = int(item.get("id"))
            status_item = status_by_id.get(zone_id)
            zones.append(
                Zone(
                    id=zone_id,
                    name=item.get("zoneName") or f"Zone {zone_id}",
                    partition_id=int(item.get("subSystemNo", 0)),
                    detector_type=item.get("detectorType", "other"),
                    zone_type=item.get("zoneType", "Instant"),
                    status=status_item.status if status_item else "notRelated",
                    alarm=status_item.alarm if status_item else False,
                    bypassed=status_item.bypassed if status_item else False,
                    tamper_evident=status_item.tamper_evident if status_item else False,
                    armed=status_item.armed if status_item else False,
                    charge=status_item.charge if status_item else "normal",
                    magnet_open_status=status_item.magnet_open_status if status_item else None,
                    charge_value=status_item.charge_value if status_item else None,
                    signal=status_item.signal if status_item else None,
                    temperature=status_item.temperature if status_item else None,
                    humidity=status_item.humidity if status_item else None,
                    zone_attrib=status_item.zone_attrib if status_item else None,
                    is_via_repeater=status_item.is_via_repeater if status_item else None,
                    stay_away=status_item.stay_away if status_item else None,
                    model=status_item.model if status_item else None,
                    version=status_item.version if status_item else None,
                    # Only the Configuration endpoint reports the detector's own serial
                    # number ("detectorSeq") -- status/zones doesn't have it at all.
                    serial_no=item.get("detectorSeq"),
                    # Likewise, only Configuration reports these two (status/zones doesn't).
                    chime_enabled=json_bool(item["chimeEnabled"]) if "chimeEnabled" in item else None,
                    silent_enabled=json_bool(item["silentEnabled"]) if "silentEnabled" in item else None,
                )
            )
        return zones

    async def get_zones_status(self) -> list[Zone]:
        """Get current status of all zones."""
        data = await self._security_cp_request(GET, "SecurityCP/status/zones")
        zones = []
        for wrapper in self._security_cp_list(data):
            item = wrapper.get("Zone", {})
            magnet_open_status = item.get("magnetOpenStatus")
            is_via_repeater = item.get("isViaRepeater")
            stay_away = item.get("stayAway")
            zones.append(
                Zone(
                    id=int(item.get("id")),
                    name=item.get("name") or f"Zone {item.get('id')}",
                    detector_type=item.get("detectorType", "other"),
                    status=item.get("status", "notRelated"),
                    alarm=json_bool(item.get("alarm", False)),
                    bypassed=json_bool(item.get("bypassed", False)),
                    tamper_evident=json_bool(item.get("tamperEvident", False)),
                    armed=json_bool(item.get("armed", False)),
                    charge=item.get("charge", "normal"),
                    magnet_open_status=json_bool(magnet_open_status) if magnet_open_status is not None else None,
                    charge_value=item.get("chargeValue"),
                    signal=item.get("signal"),
                    temperature=item.get("temperature"),
                    humidity=item.get("humidity"),
                    zone_attrib=item.get("zoneAttrib"),
                    is_via_repeater=json_bool(is_via_repeater) if is_via_repeater is not None else None,
                    stay_away=json_bool(stay_away) if stay_away is not None else None,
                    model=item.get("model"),
                    version=item.get("version"),
                )
            )
        return zones

    def get_zone_by_id(self, zone_id: int) -> Zone | None:
        """Get zone object by id."""
        for zone in self.zones:
            if zone.id == zone_id:
                return zone
        return None

    async def bypass_zone(self, zone_id: int) -> None:
        """Bypass a zone (excluded from the next arming cycle)."""
        await self._security_cp_request(PUT, f"SecurityCP/control/bypass/{zone_id}")

    async def recover_bypass_zone(self, zone_id: int) -> None:
        """Recover (un-bypass) a zone."""
        await self._security_cp_request(PUT, f"SecurityCP/control/bypassRecover/{zone_id}")

    async def set_zone_parameter(self, zone_id: int, **fields) -> None:
        """Set one or more SecurityCP/Configuration/zones parameters for a zone.

        Confirmed against the ISAPI reference that this endpoint's request fields are all
        marked optional except "id" -- a partial update, so only the given fields are sent
        rather than round-tripping the zone's entire (much larger) configuration object.
        """
        body = json.dumps({"Zone": {"id": zone_id, **fields}})
        await self._security_cp_request(PUT, f"SecurityCP/Configuration/zones/{zone_id}", data=body)

    async def set_zone_chime_enabled(self, zone_id: int, enabled: bool) -> None:
        """Enable or disable the doorbell chime for a zone."""
        await self.set_zone_parameter(zone_id, chimeEnabled=enabled)

    async def set_zone_silent_enabled(self, zone_id: int, enabled: bool) -> None:
        """Enable or disable muting the siren for a zone."""
        await self.set_zone_parameter(zone_id, silentEnabled=enabled)

    async def get_peripherals(self) -> list[Peripheral]:
        """Get security control panel peripherals (keypads, sirens, remotes, repeaters, extension modules).

        Each kind lives behind its own SecurityCP/status/* endpoint, gated by a matching
        isSpt*Mod/isSptRemoteStatus flag -- not every panel model/firmware exposes every kind
        (e.g. repeaters only exist on fully-wireless AX PRO panels, not the wired-first AX
        Hybrid PRO), so unsupported kinds are skipped rather than probed.
        """
        status_cap = (await self._security_cp_request(GET, "SecurityCP/status/capabilities")).get(
            "HostStatusCap", {}
        )

        peripherals = []
        for capability_flag, url, list_key, item_key, kind in PERIPHERAL_ENDPOINTS:
            if not json_bool(status_cap.get(capability_flag, False)):
                continue
            with suppress(Exception):
                peripherals.extend(await self._get_peripheral_list(url, list_key, item_key, kind))
        return peripherals

    async def _get_peripheral_list(self, url: str, list_key: str, item_key: str, kind: str) -> list[Peripheral]:
        """Fetch and parse one SecurityCP peripheral status endpoint."""
        data = await self._security_cp_request(GET, url)
        peripherals = []
        for wrapper in data.get(list_key) or []:
            item = wrapper.get(item_key, {})
            if not item:
                continue
            charge_value = item.get("chargeValue")
            signal = item.get("signal")
            temperature = item.get("temperature")
            peripheral_id = int(item.get("id"))
            peripherals.append(
                Peripheral(
                    kind=kind,
                    id=peripheral_id,
                    name=item.get("name") or f"{item_key} {peripheral_id}",
                    serial_no=item.get("seq"),
                    model=item.get("model"),
                    version=item.get("version"),
                    status=item.get("status"),
                    tamper_evident=json_bool(item["tamperEvident"]) if "tamperEvident" in item else None,
                    charge=item.get("charge"),
                    charge_value=int(charge_value) if charge_value is not None else None,
                    signal=int(signal) if signal is not None else None,
                    temperature=int(temperature) if temperature is not None else None,
                    mains_power=json_bool(item["mainPowerSupply"]) if "mainPowerSupply" in item else None,
                )
            )
        return peripherals

    def get_peripheral_by_kind_and_id(self, kind: str, peripheral_id: int) -> Peripheral | None:
        """Get peripheral object by kind and id."""
        for peripheral in self.peripherals:
            if peripheral.kind == kind and peripheral.id == peripheral_id:
                return peripheral
        return None

    async def get_host_status(self) -> SecurityHostStatus:
        """Get security control panel host-level status (AC power, battery, tamper, faults)."""
        data = await self._security_cp_request(GET, "SecurityCP/status/host")
        host_status = data.get("AlarmHostStatus", {}).get("HostStatus", {})
        battery_list = data.get("AlarmHostStatus", {}).get("BatteryList", [])
        battery = battery_list[0].get("Battery", {}) if battery_list else {}

        return SecurityHostStatus(
            tamper_evident=json_bool(host_status.get("tamperEvident", False)),
            ac_connected=json_bool(host_status.get("ACConnect", True)),
            fault_count=int(host_status.get("faultNum", 0)),
            battery_status=battery.get("status", "normal"),
            battery_percent=battery.get("percent"),
            battery_voltage=battery.get("voltage"),
        )

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
        response = await self.request(POST, url, present="json", data=json.dumps(data), use_forbidden_cache=False)
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
            data = await self.request(GET, event.url, use_forbidden_cache=False)
            node = self._get_event_state_node(event)
            new_state = bool_to_str(is_enabled)
            if new_state == data[node]["enabled"]:
                return
            data[node]["enabled"] = new_state
            xml = xmltodict.unparse(data)
            await self.request(PUT, event.url, present="xml", data=xml, use_forbidden_cache=False)
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
        await self.request(PUT, f"System/IO/outputs/{port_no}/trigger", present="xml", data=xml, use_forbidden_cache=False)

    async def get_holiday_enabled_state(self, holiday_index=0) -> bool:
        """Get holiday state."""

        data = await self.request(GET, "System/Holidays")
        holiday = data["HolidayList"]["holiday"][holiday_index]
        return str_to_bool(holiday["enabled"]["#text"])

    async def set_holiday_enabled_state(self, is_enabled: bool, holiday_index=0) -> None:
        """Enable or disable holiday, by enable set time span to year starting from today."""

        data = await self.request(GET, "System/Holidays", use_forbidden_cache=False)
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
        await self.request(PUT, "System/Holidays", present="xml", data=xml, use_forbidden_cache=False)

    def _get_event_notification_host(self, data: Node) -> Node:
        hosts = deep_get(data, "HttpHostNotificationList.HttpHostNotification", [])
        if hosts:
            return hosts[0]

    async def get_alarm_server(self) -> AlarmServer | None:
        """Get event notifications listener server URL."""

        data = await self.request(GET, "Event/notification/httpHosts")
        if not data:
            _LOGGER.debug("get_alarm_server[%s]: Event/notification/httpHosts returned no data", self.host)
            return None
        host = self._get_event_notification_host(data)

        alarm_server = AlarmServer(
            ip_address=host.get("ipAddress"),
            port_no=int(host.get("portNo")),
            url=host.get("url"),
            protocol_type=host.get("protocolType"),
            host_name=host.get("hostName"),
        )
        _LOGGER.debug("get_alarm_server[%s]: %s", self.host, alarm_server)
        return alarm_server

    async def set_alarm_server(self, base_url: str, path: str) -> None:
        """Set event notifications listener server."""

        address = urlparse(base_url)
        data = await self.request(GET, "Event/notification/httpHosts", use_forbidden_cache=False)
        if not data:
            _LOGGER.warning(
                "set_alarm_server[%s]: Event/notification/httpHosts returned no data, cannot configure host %s",
                self.host,
                base_url,
            )
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
            _LOGGER.info(
                "set_alarm_server[%s]: already configured to %s://%s:%s%s, no change needed",
                self.host,
                host["protocolType"],
                old_address,
                host.get("portNo"),
                host["url"],
            )
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
        _LOGGER.info(
            "set_alarm_server[%s]: updating notification host to %s://%s:%s%s",
            self.host,
            host["protocolType"],
            address.hostname,
            host["portNo"],
            path,
        )
        await self.request(PUT, "Event/notification/httpHosts", present="xml", data=xml, use_forbidden_cache=False)

    async def reboot(self):
        """Reboot device."""
        await self.request(PUT, "System/reboot", present="xml", use_forbidden_cache=False)

    @staticmethod
    def parse_event_notification_raw(xml: str) -> Node:
        """Parse EventNotificationAlert XML into a raw dict, without event-specific validation."""

        # Fix for some cameras sending non html encoded data
        xml = xml.replace("&", "&amp;")

        data = xmltodict.parse(xml)
        return data.get("EventNotificationAlert", {})

    @staticmethod
    def parse_event_notification(xml: str) -> AlertInfo:
        """Parse incoming EventNotificationAlert XML message."""

        alert = ISAPIClient.parse_event_notification_raw(xml)

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

        target_type = alert.get("targetType")

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
            target_type,
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
        elif response.status_code == 200:
            # No challenge needed. This also covers the case where a sessionLogin cookie was
            # already issued earlier on this same client: httpx's own cookie jar (shared by
            # self._session across all requests) keeps attaching it automatically even to
            # this bare, auth-less probe, so the device answers 200 here without us ever
            # setting a Cookie header ourselves. Cache a no-op auth either way, so future
            # requests stop re-probing every single time -- if the cookie ever actually
            # expires, request()'s reactive sessionLogin fallback (on a real 401) takes over.
            self._auth_method = httpx.Auth()

        if not self._auth_method:
            _LOGGER.error("Authentication method not detected, %s", response.status_code)
            if response.headers:
                _LOGGER.error("response.headers %s", response.headers)

    async def _fallback_to_session_login(
        self, method: str, full_url: str, data: str | None, unauthorized: httpx.Response
    ) -> httpx.Response:
        """Retry once via ISAPI "sessionLogin" after Basic/Digest is rejected outright.

        Some devices advertise Basic/Digest but reject the plain configured password
        regardless -- confirmed with an AX Hybrid PRO panel once it's been added to a
        Hik-Connect account -- and require this hashed challenge-response flow instead. This
        is deliberately reactive (tried only after a real request actually fails), never a
        proactive probe: some devices (an NVR, in testing) advertise/parse a sessionLogin
        capabilities response too but don't actually support logging in through it, so
        preferring it up front broke a previously-working plain login for those.
        """
        _LOGGER.info(
            "%s rejected %s; falling back to sessionLogin",
            self.host,
            type(self._auth_method).__name__,
        )
        previous_auth = self._auth_method
        self._auth_method = SessionLoginAuth(self.username, self.password)
        response = await self._session.request(
            method,
            full_url,
            auth=self._auth_method,
            data=data,
            timeout=self.timeout,
        )
        if not self._auth_method.cookie:
            # sessionLogin unsupported or also rejected (e.g. a plain wrong password): keep the
            # original auth method and surface the original 401, not the capabilities response.
            self._auth_method = previous_auth
            return unauthorized
        return response

    def get_isapi_url(self, relative_url: str) -> str:
        """Build full ISAPI URL."""
        return f"{self.host}/{self.isapi_prefix}/{relative_url}"

    async def request(
        self,
        method: str,
        url: str,
        present: str = "dict",
        data: str = None,
        *,
        use_forbidden_cache: bool = True,
    ) -> Any:
        """Send ISAPI request and log response, returns {} if request fails."""
        full_url = self.get_isapi_url(url)
        cache_key = f"{method} {full_url}"
        if use_forbidden_cache and not self.pending_initialization and cache_key in self._forbidden_cache:
            raise ISAPIForbiddenError(url=full_url, method=method, suppressed=True)
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
            if response.status_code == HTTPStatus.UNAUTHORIZED and not isinstance(self._auth_method, SessionLoginAuth):
                response = await self._fallback_to_session_login(method, full_url, data, response)
            response.raise_for_status()
            self._forbidden_cache.discard(cache_key)
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
                self._forbidden_cache.add(cache_key)
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

    @staticmethod
    def _decode_arming_part(data: bytes) -> str:
        """Decode a single arming-stream part body, tolerating non-UTF-8 content.

        Content-Type on these parts claims charset="UTF-8", but some panels/NVRs embed
        user-configured strings (e.g. zone/partition names with accented characters) encoded
        with the device's own locale codepage instead, breaking strict UTF-8 decoding. Latin-1
        never raises (every byte maps to a code point), so it's used as a last-resort fallback
        to avoid silently dropping the whole event just because one field's bytes are off.
        """
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("latin-1")

    async def open_arming_stream(self) -> AsyncIterator[Node]:
        """Open an ISAPI "arming with subscription" connection and yield events as they arrive.

        Security control panels (SecurityCP) don't support the generic ISAPI "listening mode"
        (Event/notification/httpHosts) for their own zone/partition/CID events -- that
        mechanism is only used by cameras/NVRs. Real-time delivery instead requires "arming":
        keeping a persistent connection open and reacting as the device pushes multipart parts
        (JSON or XML) down it, plus periodic heartbeats.

        Uses "arming WITH subscription" (POST Event/notification/subscribeEvent) rather than
        the simpler "without subscription" (GET Event/notification/alertStream): confirmed
        against real hardware that the device's own SubscribeEventCap (GET
        Event/notification/subscribeEventCap) is where it advertises cidEvent support, and
        arm/disarm events only ever showed up on this POST-based connection during testing --
        plain GET alertStream was never actually verified to deliver cidEvent traffic.
        """
        if not self._auth_method:
            await self._detect_auth_method()

        full_url = self.get_isapi_url("Event/notification/subscribeEvent")
        timeout = httpx.Timeout(connect=self.timeout, read=None, write=self.timeout, pool=self.timeout)
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<SubscribeEvent version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
            "<heartbeat>6</heartbeat>"
            "<eventMode>all</eventMode>"
            "</SubscribeEvent>"
        )

        async with self._session.stream(
            "POST",
            full_url,
            auth=self._auth_method,
            timeout=timeout,
            headers={"Connection": "keep-alive", "Content-Type": "application/xml"},
            content=body,
        ) as response:
            response.raise_for_status()
            boundary = extract_boundary(response.headers.get("content-type", ""))
            if not boundary:
                raise ValueError(f"subscribeEvent response has no multipart boundary: {response.headers}")

            parser = MultipartStreamParser(boundary)
            chunks = response.aiter_bytes().__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(chunks.__anext__(), timeout=ARMING_IDLE_TIMEOUT)
                except StopAsyncIteration:
                    return

                for headers, part_body in parser.feed(chunk):
                    part_content_type = headers.get("content-type", "")
                    try:
                        if "json" in part_content_type:
                            yield json.loads(self._decode_arming_part(part_body))
                        elif "xml" in part_content_type:
                            parsed = xmltodict.parse(self._decode_arming_part(part_body))
                            if "SubscribeEventResponse" in parsed:
                                # First message on the connection: subscription acknowledgment,
                                # not an event -- nothing to act on.
                                _LOGGER.debug("Arming subscription established: %s", parsed["SubscribeEventResponse"])
                                continue
                            yield parsed.get("EventNotificationAlert", {})
                        # else: binary picture data etc. attached to the event -- ignore
                    except Exception as ex:  # pylint: disable=broad-except
                        _LOGGER.warning("Cannot parse arming stream part (%s): %s", part_content_type, ex)


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

    def __init__(
        self,
        ex: HTTPStatusError | None = None,
        *args,
        url: str | None = None,
        method: str = GET,
        suppressed: bool = False,
    ) -> None:
        """Initialize exception."""
        self.suppressed = suppressed
        if ex:
            self.url = str(ex.request.url)
            self.response = ex.response
        else:
            self.url = url or ""
            request = httpx.Request(method, self.url) if self.url else None
            self.response = httpx.Response(HTTPStatus.FORBIDDEN, request=request)
        self.message = f"Forbidden request {self.url}, check user permissions."
        super().__init__(self.message)
