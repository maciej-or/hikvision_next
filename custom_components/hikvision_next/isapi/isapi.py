"""Hikvision ISAPI client."""

from __future__ import annotations

import asyncio
import traceback
from contextlib import suppress
import datetime
from http import HTTPStatus
import ipaddress
import json
import logging
from typing import TYPE_CHECKING, Any, AsyncIterator
from urllib.parse import quote, urljoin, urlparse

import httpx
from httpx import HTTPStatusError
import xmltodict
import time

from homeassistant.const import (
    STATE_ON, STATE_OFF
)
from ..door_control import DoorControlAction, ISAPI_REMOTE_CONTROL_DOOR_CMD
from .const import (
    CONNECTION_TYPE_DIRECT,
    CONNECTION_TYPE_PROXIED,
    EVENT_BASIC,
    EVENT_IO,
    EVENT_PIR,
    EVENT_TRAFFIC,
    EVENTS,
    EVENTS_ALTERNATE_ID,
    FACE_SNAP_EVENT_IDS,
    EVENT_TRIGGER_PREFIXES,
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
from .utils import (
    bool_to_str,
    channel_from_bitmap,
    deep_get,
    input_proxy_cap_indicates_ptz,
    parse_isapi_response,
    ptz_channel_cap_indicates_support,
    str_to_bool,
)
import hashlib

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


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
            hass: HomeAssistant | None = None,
    ) -> None:
        """Initialize."""

        self.host = host
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = 20
        self.isapi_prefix = "ISAPI"
        self._hass = hass
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
        self._streaming_channels_cache: list[dict] | None = None

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

        from ..sdk.video_intercom import is_video_intercom_device

        self.capabilities.support_video_intercom = is_video_intercom_device(
            device_type=self.device_info.device_type,
            model=self.device_info.model,
        )

        # Set if NVR based on whether more than 1 supported IP or analog cameras
        # Single IP camera will show 0 supported devices in total
        if self.capabilities.analog_cameras_inputs + self.capabilities.digital_cameras_inputs > 1:
            self.device_info.is_nvr = True

        await self.get_cameras()
        await self.detect_ptz_support(capabilities)

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

    @staticmethod
    def _channel_description_is_ptz(channel_description: str | None) -> bool:
        return str(channel_description or "").strip().upper() == "PTZ"

    def _ptz_control_base_url(self, camera: AnalogCamera) -> str:
        """Return the ISAPI base path for PTZ commands on a channel."""
        if isinstance(camera, IPCamera) and camera.connection_type == CONNECTION_TYPE_PROXIED:
            return f"ContentMgmt/PTZCtrlProxy/channels/{camera.id}"
        return f"PTZCtrl/channels/{camera.id}"

    async def _load_channel_descriptions(self) -> dict[int, str]:
        """Load channelDescription values from video input channels."""
        channel_descriptions: dict[int, str] = {}
        try:
            analog_cameras = deep_get(
                (await self.request(GET, "System/Video/inputs/channels", quiet=True)),
                "VideoInputChannelList.VideoInputChannel",
                [],
            )
            if not isinstance(analog_cameras, list):
                analog_cameras = [analog_cameras] if analog_cameras else []
            for analog_camera in analog_cameras:
                if analog_camera.get("id") is not None:
                    channel_descriptions[int(analog_camera["id"])] = analog_camera.get("channelDescription", "")
        except Exception:
            pass
        return channel_descriptions

    def _ptz_capabilities_indicate_support(
        self,
        response: dict,
        *,
        proxied: bool,
    ) -> bool:
        """Parse a PTZ capabilities response and decide if the channel supports PTZ."""
        if not response:
            return False
        ptz_cap = deep_get(response, "PTZChannelCap")
        return ptz_channel_cap_indicates_support(ptz_cap, proxied=proxied)

    async def _input_proxy_channel_supports_ptz(self, camera: IPCamera) -> bool:
        """Check InputProxy channel capabilities for proxied PTZ support."""
        try:
            response = await self.request(
                GET,
                f"ContentMgmt/InputProxy/channels/{camera.id}/capabilities",
                quiet=True,
            )
            return input_proxy_cap_indicates_ptz(response)
        except Exception:
            return False

    async def _channel_supports_ptz(self, camera: AnalogCamera) -> bool:
        """Probe whether a channel exposes PTZ control endpoints."""
        proxied = isinstance(camera, IPCamera) and camera.connection_type == CONNECTION_TYPE_PROXIED
        candidates = [self._ptz_control_base_url(camera)]
        if not proxied and not self.device_info.is_nvr:
            candidates.append(f"PTZCtrl/channels/{camera.id}")

        for base_url in dict.fromkeys(candidates):
            try:
                response = await self.request(GET, f"{base_url}/capabilities", quiet=True)
                if self._ptz_capabilities_indicate_support(response, proxied=proxied):
                    return True
            except Exception:
                continue

        if proxied:
            return await self._input_proxy_channel_supports_ptz(camera)
        return False

    async def detect_ptz_support(self, system_capabilities: dict) -> None:
        """Mark cameras that expose PTZ control."""
        channel_descriptions = await self._load_channel_descriptions()

        for camera in self.cameras:
            if self._channel_description_is_ptz(channel_descriptions.get(camera.id)):
                camera.support_ptz = True
                continue
            if await self._channel_supports_ptz(camera):
                camera.support_ptz = True

    @staticmethod
    def _ptz_data_xml_plain(pan: int, tilt: int, zoom: int) -> str:
        return (
            "<PTZData>"
            f"<pan>{pan}</pan><tilt>{tilt}</tilt><zoom>{zoom}</zoom>"
            "</PTZData>"
        )

    def _ptz_data_xml(self, pan: int, tilt: int, zoom: int) -> str:
        return xmltodict.unparse(
            {
                "PTZData": {
                    "@xmlns": "http://www.isapi.org/ver20/XMLSchema",
                    "@version": "2.0",
                    "pan": str(pan),
                    "tilt": str(tilt),
                    "zoom": str(zoom),
                }
            }
        )

    def _ptz_payload_variants(self, pan: int, tilt: int, zoom: int) -> list[str]:
        return list(
            dict.fromkeys(
                [
                    self._ptz_data_xml_plain(pan, tilt, zoom),
                    self._ptz_data_xml(pan, tilt, zoom),
                ]
            )
        )

    def _ptz_continuous_paths(self, base_url: str) -> list[str]:
        return list(dict.fromkeys([f"{base_url}/continuous", f"{base_url}/Continuous"]))

    def _ptz_stop_paths(self, base_url: str) -> list[str]:
        return list(dict.fromkeys([f"{base_url}/stop", f"{base_url}/Stop"]))

    async def ptz_continuous(
        self,
        camera: AnalogCamera,
        pan: int = 0,
        tilt: int = 0,
        zoom: int = 0,
    ) -> None:
        """Start or adjust continuous PTZ movement on a channel."""
        if not camera.support_ptz:
            raise ValueError(f"Camera channel {camera.id} does not support PTZ")
        base_url = self._ptz_control_base_url(camera)
        last_error: Exception | None = None
        for path in self._ptz_continuous_paths(base_url):
            for payload in self._ptz_payload_variants(pan, tilt, zoom):
                try:
                    await self.request(PUT, path, present="xml", data=payload)
                    _LOGGER.debug("PTZ continuous ok on %s", path)
                    return
                except Exception as ex:
                    last_error = ex
        if last_error:
            raise last_error

    async def ptz_stop(self, camera: AnalogCamera) -> None:
        """Stop PTZ movement on a channel."""
        base_url = self._ptz_control_base_url(camera)
        stop_payload = self._ptz_data_xml_plain(0, 0, 0)
        for path in self._ptz_stop_paths(base_url):
            try:
                await self.request(PUT, path, present="xml", data=stop_payload)
                _LOGGER.debug("PTZ stop ok on %s", path)
                return
            except Exception:
                continue
        await self.ptz_continuous(camera, pan=0, tilt=0, zoom=0)

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

    @staticmethod
    def _parse_cap_support_flags(cap_dict: dict) -> list[str]:
        """Parse isSupport* capability flags into normalized event ids."""
        if not cap_dict:
            return []
        return [
            key.lower().replace("issupport", "")
            for key, value in cap_dict.items()
            if key.startswith("isSupport") and str_to_bool(value)
        ]

    async def _get_ipc_supported_event_types(self, system_capabilities: dict) -> list[str]:
        """Get supported event types for single-channel IPC from Event/Smart capabilities."""
        event_types: list[str] = []

        event_cap_raw = await self.request(GET, "Event/capabilities")
        event_cap = event_cap_raw.get("EventCap") or deep_get(system_capabilities, "EventCap", {})
        event_types.extend(self._parse_cap_support_flags(event_cap))

        smart_cap_raw = await self.request(GET, "Smart/capabilities")
        smart_cap = smart_cap_raw.get("SmartCap") or deep_get(system_capabilities, "SmartCap", {})
        event_types.extend(self._parse_cap_support_flags(smart_cap))

        if str_to_bool(deep_get(system_capabilities, "WLAlarmCap.isSupportPIR", False)):
            event_types.append(EVENT_PIR)

        return list(dict.fromkeys(event_types))

    async def _load_event_triggers(self) -> list:
        """Load all configured event triggers from Event/triggers."""
        event_triggers = await self.request(GET, "Event/triggers")
        event_notification = event_triggers.get("EventNotification")
        if event_notification:
            return deep_get(event_notification, "EventTriggerList.EventTrigger", [])
        return deep_get(event_triggers, "EventTriggerList.EventTrigger", [])

    @staticmethod
    def _parse_event_type_options(event_type_node) -> list[str]:
        """Parse event type option lists from ChannelEventCap."""
        if isinstance(event_type_node, dict):
            opts = event_type_node.get("@opt") or event_type_node.get("opt") or ""
            return [event_type.strip() for event_type in opts.split(",") if event_type.strip()]
        if isinstance(event_type_node, str):
            return [event_type_node.strip()] if event_type_node.strip() else []
        return []

    @staticmethod
    def _normalize_event_id(event_type: str) -> str | None:
        """Normalize Hikvision event type strings to canonical event ids."""
        if not event_type:
            return None
        event_id = str(event_type).lower().strip()
        if event_id in EVENTS_ALTERNATE_ID:
            event_id = EVENTS_ALTERNATE_ID[event_id]
        if event_id not in EVENTS:
            return None
        return event_id

    def _event_trigger_request_ids(self, event_id: str, channel_id: int) -> list[str]:
        """Build candidate Event/triggers request ids for a channel event."""
        prefixes = list(EVENT_TRIGGER_PREFIXES.get(event_id, [event_id]))
        for alt_id, canonical_id in EVENTS_ALTERNATE_ID.items():
            if canonical_id == event_id and alt_id not in prefixes:
                prefixes.append(alt_id)
        slug = EVENTS.get(event_id, {}).get("slug")
        if slug and slug not in prefixes:
            prefixes.append(slug)
        return list(dict.fromkeys(f"{prefix}-{channel_id}" for prefix in prefixes))

    async def _fetch_event_trigger(self, channel_id: int, event_id: str) -> dict:
        """Fetch EventTrigger, trying alternate Hikvision trigger id formats."""
        for trigger_id in self._event_trigger_request_ids(event_id, channel_id):
            try:
                event_trigger = await self.request(GET, f"Event/triggers/{trigger_id}", quiet=True)
            except Exception:
                continue
            event_trigger = deep_get(event_trigger, "EventTrigger", {})
            if event_trigger:
                return event_trigger
        return {}

    async def _get_per_channel_event_capabilities(self) -> list[dict]:
        """Fetch Event/channels/{id}/capabilities for each known camera channel."""
        channel_events = []
        for camera in self.cameras:
            try:
                channel_cap = await self.request(GET, f"Event/channels/{camera.id}/capabilities", quiet=True)
            except Exception as ex:
                _LOGGER.debug(
                    "Per-channel event capabilities unavailable for %s channel %s: %s",
                    self.host,
                    camera.id,
                    ex,
                )
                continue
            event_cap = channel_cap.get("ChannelEventCap")
            if not event_cap:
                continue
            if not event_cap.get("channelID"):
                event_cap = {**event_cap, "channelID": camera.id}
            channel_events.append(event_cap)
        return channel_events

    async def _get_channel_event_capabilities_list(self, system_capabilities: dict) -> list[dict]:
        """Discover per-channel event types from bulk or per-channel capability endpoints."""
        channel_events: list[dict] = []

        if str_to_bool(deep_get(system_capabilities, "isSupportChannelEventCap", "false")):
            channels_capabilities = await self.request(GET, "Event/channels/capabilities", quiet=True)
            channel_events = deep_get(channels_capabilities, "ChannelEventCapList.ChannelEventCap", [])
            if channel_events and not isinstance(channel_events, list):
                channel_events = [channel_events]

        if channel_events:
            return channel_events

        _LOGGER.debug(
            "Bulk Event/channels/capabilities unavailable for %s, trying per-channel endpoints",
            self.host,
        )
        return await self._get_per_channel_event_capabilities()

    def _create_event_info_from_capability(self, channel_id: int, event_id: str) -> EventInfo | None:
        """Create a minimal EventInfo when trigger details are unavailable."""
        camera = self.get_camera_by_id(channel_id)
        is_proxy = bool(
            camera
            and isinstance(camera, IPCamera)
            and camera.connection_type == CONNECTION_TYPE_PROXIED
        )
        url = self.get_event_url(event_id, channel_id, 0, is_proxy)
        if not url:
            return None
        return EventInfo(
            channel_id=channel_id,
            io_port_id=0,
            id=event_id,
            url=url,
            is_proxy=is_proxy,
            notifications=[],
        )

    async def _append_events_from_channel_caps(
        self,
        events: list[EventInfo],
        channel_event_caps: list[dict],
        create_event_info,
    ) -> None:
        """Create EventInfo entries from channel capability lists."""
        for event_cap in channel_event_caps:
            event_types = self._parse_event_type_options(deep_get(event_cap, "eventType"))
            if not event_types:
                continue

            channel_id = int(event_cap.get("channelID") or 0)
            if not channel_id:
                continue

            for event_type in event_types:
                event_id = self._normalize_event_id(event_type)
                if not event_id:
                    continue
                if any(e.id == event_id and e.channel_id == channel_id for e in events):
                    continue

                event_trigger = await self._fetch_event_trigger(channel_id, event_id)
                if event := create_event_info(event_trigger):
                    events.append(event)
                elif event := self._create_event_info_from_capability(channel_id, event_id):
                    events.append(event)

    async def _fill_missing_multichannel_triggers(
        self,
        events: list[EventInfo],
        system_capabilities: dict,
        create_event_info,
    ) -> None:
        """Probe per-channel triggers when channel capability endpoints are unavailable."""
        supported_event_ids = await self._get_ipc_supported_event_types(system_capabilities)
        for camera in self.cameras:
            for event_id in supported_event_ids:
                if event_id not in EVENTS:
                    continue
                if any(e.id == event_id and e.channel_id == camera.id for e in events):
                    continue
                event_trigger = await self._fetch_event_trigger(camera.id, event_id)
                if event := create_event_info(event_trigger):
                    events.append(event)
                elif event := self._create_event_info_from_capability(camera.id, event_id):
                    events.append(event)

    async def get_supported_events(self, system_capabilities: dict) -> list[EventInfo]:
        """Get list of all supported events available."""

        def create_event_info(event_trigger: dict):
            notification_list = event_trigger.get("EventTriggerNotificationList", {}) or {}

            event_type = event_trigger.get("eventType")
            event_id = self._normalize_event_id(event_type)
            if not event_id:
                return None

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
            # NVR: load configured triggers, then supplement from per-channel capabilities.
            for event_trigger in await self._load_event_triggers():
                if event := create_event_info(event_trigger):
                    events.append(event)

            channel_event_caps = await self._get_per_channel_event_capabilities()
            if channel_event_caps:
                await self._append_events_from_channel_caps(events, channel_event_caps, create_event_info)

        elif not self.capabilities.is_multi_channel:
            # Single-channel IPC: discover via Event/capabilities + Smart/capabilities,
            # then load configured triggers from Event/triggers.
            supported_event_ids = await self._get_ipc_supported_event_types(system_capabilities)
            channel_id = self.cameras[0].id if self.cameras else 1

            for event_trigger in await self._load_event_triggers():
                if event := create_event_info(event_trigger):
                    events.append(event)

            for event_id in supported_event_ids:
                if event_id not in EVENTS:
                    continue
                if any(e.id == event_id and e.channel_id == channel_id for e in events):
                    continue
                event_trigger = await self._fetch_event_trigger(channel_id, event_id)
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

        # Multichannel IPC only; NVR already loaded all triggers above.
        if self.capabilities.is_multi_channel and not self.device_info.is_nvr:
            for event_trigger in await self._load_event_triggers():
                if event := create_event_info(event_trigger):
                    events.append(event)

            channel_event_caps = await self._get_channel_event_capabilities_list(system_capabilities)
            if channel_event_caps:
                await self._append_events_from_channel_caps(events, channel_event_caps, create_event_info)
            else:
                _LOGGER.debug(
                    "Channel event capabilities unavailable for %s, probing per-channel triggers",
                    self.host,
                )
                await self._fill_missing_multichannel_triggers(events, system_capabilities, create_event_info)

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

        # ANPR: standalone devices use subscribeEventCap; NVR uses per-channel triggers.
        if not self.device_info.is_nvr and (
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

    @staticmethod
    def _stream_belongs_to_channel(stream_id: str | int, channel_id: int) -> bool:
        """Return True when a streaming channel id belongs to a camera channel (e.g. 5201 -> 52)."""
        return int(stream_id) // 100 == int(channel_id)

    @staticmethod
    def _streaming_channel_has_detail(stream_info: dict) -> bool:
        return bool(deep_get(stream_info, "Video.videoCodecType"))

    @staticmethod
    def _parse_streaming_channel(stream_info: dict) -> CameraStreamInfo | None:
        if not stream_info or not stream_info.get("id"):
            return None

        stream_id = int(stream_info["id"])
        stream_type_id = stream_id % 100
        stream_type = STREAM_TYPE.get(stream_type_id, f"Stream {stream_type_id}")
        return CameraStreamInfo(
            id=stream_id,
            name=stream_info.get("channelName", str(stream_id)),
            type_id=stream_type_id,
            type=stream_type,
            enabled=str_to_bool(stream_info.get("enabled", "false")),
            codec=deep_get(stream_info, "Video.videoCodecType", ""),
            width=int(deep_get(stream_info, "Video.videoResolutionWidth", 0) or 0),
            height=int(deep_get(stream_info, "Video.videoResolutionHeight", 0) or 0),
            audio=str_to_bool(deep_get(stream_info, "Audio.enabled", "false")),
        )

    async def _load_streaming_channel_list(self) -> list[dict]:
        """Load and cache the streaming channel list from ISAPI."""
        if self._streaming_channels_cache is not None:
            return self._streaming_channels_cache

        channel_list: list[dict] = []
        try:
            streaming_channels = await self.request(GET, "Streaming/channels")
            channel_list = deep_get(streaming_channels, "StreamingChannelList.StreamingChannel", [])
            if channel_list and not isinstance(channel_list, list):
                channel_list = [channel_list]
        except Exception as ex:
            _LOGGER.debug("Streaming channel list unavailable for %s: %s", self.host, ex)

        self._streaming_channels_cache = channel_list
        return channel_list

    async def _get_streaming_channel_detail(self, stream_id: str | int) -> dict | None:
        stream_info = (await self.request(GET, f"Streaming/channels/{stream_id}", quiet=True)).get("StreamingChannel")
        return stream_info or None

    async def get_camera_streams(self, channel_id: int) -> list[CameraStreamInfo]:
        """Get stream info for a camera channel using the streaming channel list when available."""
        streams: list[CameraStreamInfo] = []
        channel_list = await self._load_streaming_channel_list()
        matching_streams = [
            stream_info for stream_info in channel_list if self._stream_belongs_to_channel(stream_info.get("id"), channel_id)
        ]

        for stream_summary in matching_streams:
            stream_info = stream_summary
            if not self._streaming_channel_has_detail(stream_summary):
                stream_info = await self._get_streaming_channel_detail(stream_summary["id"])
            if stream := self._parse_streaming_channel(stream_info):
                streams.append(stream)

        if streams:
            return streams

        # Fallback when the bulk list is unavailable: probe common stream types only.
        stream_type_ids = list(STREAM_TYPE)
        if self.device_info.is_nvr:
            stream_type_ids = [1, 2, 4]

        for stream_type_id in stream_type_ids:
            stream_id = f"{channel_id}0{stream_type_id}"
            stream_info = await self._get_streaming_channel_detail(stream_id)
            if stream := self._parse_streaming_channel(stream_info):
                streams.append(stream)

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

    async def remote_control_door(
        self,
        event: EventInfo,
        action: DoorControlAction,
    ) -> None:
        """Remotely control an ACS door via ISAPI RemoteControlDoor."""
        if not event.url:
            raise ValueError(f"Cannot control door lock without URL for {event.id}")

        cmd = ISAPI_REMOTE_CONTROL_DOOR_CMD[action]
        data = {
            "RemoteControlDoor": {
                "@xmlns": "http://www.isapi.org/ver20/XMLSchema",
                "@version": "2.0",
                "cmd": cmd,
            }
        }
        xml = xmltodict.unparse(data)
        _LOGGER.info("Remote door control %s -> %s", event.url, cmd)
        await self.ext_request(PUT, event.url, present="xml", data=xml)

    async def get_event_enabled_state(self, event: EventInfo) -> bool:
        """Get event detection state."""
        if not event.url:
            _LOGGER.debug("Cannot fetch event enabled state. Unknown event URL %s", event.id)
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
            _LOGGER.debug("Cannot set event enabled state. Unknown event URL %s", event.id)
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
    def _acs_event_int(value) -> int:
        """Coerce ACS major/minor event fields from SDK, XML, or JSON."""
        if value is None:
            return 0
        if isinstance(value, str):
            return int(value, 0) if value.lower().startswith("0x") else int(value)
        return int(value)

    @staticmethod
    def _extract_anpr_metadata(alert: dict) -> tuple[str | None, str | None, int]:
        """Extract license plate metadata from ANPR / vehicle-detection payloads."""
        anpr_block = alert.get("ANPR") or alert.get("anpr") or {}
        if not isinstance(anpr_block, dict):
            anpr_block = {}

        license_plate = (
            anpr_block.get("licensePlate")
            or anpr_block.get("plateNo")
            or anpr_block.get("plateNumber")
            or deep_get(alert, "vehicleMonitor.licensePlate")
            or deep_get(alert, "VehicleInfo.licensePlate")
            or deep_get(alert, "vehicleInfo.licensePlate")
            or alert.get("licensePlate")
            or alert.get("plateNo")
            or alert.get("plateNumber")
        )
        if isinstance(license_plate, str):
            license_plate = license_plate.strip() or None

        direction = (
            anpr_block.get("direction")
            or anpr_block.get("vehicleDirection")
            or anpr_block.get("carDirection")
            or alert.get("direction")
        )
        if isinstance(direction, str):
            direction = direction.strip() or None

        confidence_raw = (
            anpr_block.get("confidenceLevel")
            or anpr_block.get("confidencelevel")
            or anpr_block.get("confidence")
            or alert.get("confidenceLevel")
            or 0
        )
        try:
            confidence = int(confidence_raw)
        except (TypeError, ValueError):
            confidence = 0

        return license_plate, direction, confidence

    @staticmethod
    def _face_person_fields(ace: dict) -> tuple[str | None, str | None, str | None]:
        """Extract person name, employee number, and card number from ACS payload."""
        employee_raw = ace.get("employeeNoString")
        if employee_raw is None:
            employee_raw = ace.get("employeeNo")
        employee = str(employee_raw).strip() if employee_raw not in (None, "") else None
        name = str(ace.get("name") or "").strip() or None
        card_no = str(ace.get("cardNo") or "").strip() or None
        return name, employee, card_no

    @staticmethod
    def _face_display_state(name: str | None, employee: str | None) -> str:
        if name:
            return name
        if employee:
            return employee
        return "unknown"

    @staticmethod
    def _parse_access_controller_event(alert: dict) -> AlertInfo | None:
        """Parse ACS AccessControllerEvent payloads into AlertInfo."""
        from ..sdk.acsalarminfo import ACS_FACE_VERIFY_PASS_MINORS

        ace = alert.get("AccessControllerEvent") or {}
        major = ISAPIClient._acs_event_int(ace.get("majorEventType"))
        minor = ISAPIClient._acs_event_int(ace.get("subEventType"))
        mac = alert.get("macAddress")
        serial = alert.get("serial")

        if major == 5:
            if minor in (0x15, 0x16, 0x13, 0x14):
                return AlertInfo(
                    0,
                    0,
                    "lock",
                    serial,
                    mac,
                    None,
                    None,
                    STATE_ON if minor in (0x15, 0x13) else STATE_OFF,
                )
            if minor in (0x19, 0x1a):
                return AlertInfo(
                    0,
                    0,
                    "door",
                    serial,
                    mac,
                    None,
                    None,
                    STATE_ON if minor == 0x19 else STATE_OFF,
                )
            if minor in ACS_FACE_VERIFY_PASS_MINORS:
                name, employee, card_no = ISAPIClient._face_person_fields(ace)
                return AlertInfo(
                    0,
                    0,
                    "face",
                    serial,
                    mac,
                    None,
                    None,
                    ISAPIClient._face_display_state(name, employee),
                    face_person_name=name,
                    face_employee_no=employee,
                    face_card_no=card_no,
                )

        if major == 3:
            lock_on = minor in (0x400, 0x402, 0x41d, 0x41e)
            lock_off = minor in (0x401, 0x403, 0x41c)
            if lock_on or lock_off:
                return AlertInfo(
                    0,
                    0,
                    "lock",
                    serial,
                    mac,
                    None,
                    None,
                    STATE_ON if lock_on else STATE_OFF,
                )

        return None

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
            return ISAPIClient._parse_access_controller_event(alert)
        else:
            normalized_event_id = ISAPIClient._normalize_event_id(event_id)
            if not normalized_event_id:
                _LOGGER.info("Unsupported subscribed event type: %s", event_id)
                return None
            event_id = normalized_event_id

            channel_id = int(
                alert.get("channelID", alert.get("dynChannelID", alert.get("videoInputChannelID", 0)))
            )
            if not channel_id:
                channel_id = channel_from_bitmap(alert.get("channels"))
            io_port_id = int(alert.get("inputIOPortID", 0))
            # <EventNotificationAlert version="1.0"
            device_serial = deep_get(alert, "Extensions.serialNumber.#text") or alert.get("serial")
            # <EventNotificationAlert version="2.0"
            mac = alert.get("macAddress")

            detection_target = deep_get(alert, "DetectionRegionList.DetectionRegionEntry.detectionTarget")
            region_id = int(deep_get(alert, "DetectionRegionList.DetectionRegionEntry.regionID", 0))

            if event_id in FACE_SNAP_EVENT_IDS:
                return None

            if not EVENTS.get(event_id):
                _LOGGER.info("Unsupported subscribed event id after normalization: %s", event_id)
                return None

            anpr_license_plate = None
            anpr_direction = None
            anpr_confidence_level = 0
            if event_id == "anpr":
                anpr_license_plate, anpr_direction, anpr_confidence_level = (
                    ISAPIClient._extract_anpr_metadata(alert)
                )

            return AlertInfo(
                channel_id,
                io_port_id,
                event_id,
                device_serial,
                mac,
                region_id,
                detection_target,
                STATE_ON,
                anpr_license_plate,
                anpr_direction,
                anpr_confidence_level,
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

    async def _create_httpx_client(self) -> httpx.AsyncClient:
        """Create an httpx client without blocking the event loop."""
        if self._hass is not None:
            from homeassistant.helpers.httpx_client import create_async_httpx_client

            return create_async_httpx_client(
                self._hass,
                self.verify_ssl,
                auto_cleanup=False,
                timeout=self.timeout,
            )

        return await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: httpx.AsyncClient(timeout=self.timeout, verify=self.verify_ssl),
        )

    async def _detect_auth_method(self):
        """Establish the connection with device."""
        if not self._session:
            self._session = await self._create_httpx_client()

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
            self._ext_session = await self._create_httpx_client()

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
            quiet: bool = False,
    ) -> Any:
        """Send ISAPI request and log response, returns {} if request fails."""
        full_url = self.get_isapi_url(url)
        response = None
        try:

            if hasattr(self, "sdk_isapi") and present == "xml":
                response = self.sdk_isapi(method, f"/{self.isapi_prefix}/{url}", data).decode('utf-8')
            else:
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
            log = _LOGGER.debug if quiet else _LOGGER.info
            log("--- [%s] %s\n%s", method, full_url, ex)
            if response is not None and response.status_code not in (200, 404):
                log("--- [%s] %d Error: %s", method, response.status_code, response.text)
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
            if hasattr(self, "sdk_isapi") and present == "xml":
                response = self.sdk_isapi(method, f"/{self.isapi_prefix}/{url}", data).decode('utf-8')
            else:
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
