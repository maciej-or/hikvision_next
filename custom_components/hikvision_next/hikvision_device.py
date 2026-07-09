"ISAPI client for Home Assistant integration."
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import logging
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util import slugify

from .const import (
    ALARM_SERVER_PATH,
    ANPR_IMAGE_SUFFIX,
    ANPR_LICENSE_PLATE_SENSOR_SUFFIX,
    CONF_ALARM_SERVER_HOST,
    CONF_CONNECTION_HTTP_NOTIFY,
    CONF_SET_ALARM_SERVER,
    DEVICE_LEVEL_EVENT_IDS,
    DOMAIN,
    HIKVISION_EVENT,
    resolve_connection_type,
    EVENTS,
    EVENTS_COORDINATOR,
    RTSP_PORT_FORCED,
    SECONDARY_COORDINATOR, CONF_CONNECTION_SDK,
)

from .coordinator import (
    EventsCoordinator,
    IntercomStatusCoordinator,
    SecondaryCoordinator,
    SubscribeStatusCoordinator,
)
from .isapi import (
    AlertInfo,
    AnalogCamera,
    EventInfo,
    EventSubscription,
    IPCamera,
    ISAPIClient,
    ISAPIForbiddenError,
    ISAPIUnauthorizedError,
)
from .isapi.const import EVENT_IO
from .isapi.utils import (
    channel_from_bitmap,
    ptz_command_from_action,
    ptz_command_from_velocities,
    ptz_prefers_isapi,
    ptz_sdk_channel_candidates,
)

from ctypes import (byref, CFUNCTYPE, POINTER, c_void_p, cast, sizeof, c_long, c_char, c_byte, memmove, c_char_p,
                    addressof)
from .sdk import hcnetsdk
from .sdk.hcnetsdk import NET_DVR_DEVICEINFO_V30, NET_DVR_SETUPALARM_PARAM_V50, NET_DVR_XML_CONFIG_INPUT, \
    NET_DVR_XML_CONFIG_OUTPUT, COMM_VEHICLE_CONTROL_ALARM, NET_DVR_VEHICLE_CONTROL_ALARM, COMM_ALARM_RULE, \
    COMM_UPLOAD_FACESNAP_RESULT, COMM_SNAP_MATCH_ALARM, COMM_VEHICLE_CONTROL_LIST_DSALARM, COMM_ITS_GATE_ALARMINFO, \
    COMM_UPLOAD_PLATE_RESULT, COMM_ITS_PLATE_RESULT, COMM_ALARM, COMM_ALARM_V40, NET_VCA_RULE_ALARM, NET_VCA_FACESNAP_RESULT, \
    NET_VCA_FACESNAP_MATCH_ALARM, NET_ITS_PLATE_RESULT, NET_DVR_PLATE_RESULT, NET_DVR_VEHICLE_CONTROL_LIST_DSALARM, \
    NET_DVR_GATE_ALARMINFO, NET_DVR_ALARMINFO, NET_DVR_ALARMINFO_V40, \
    MAX_CHANNUM_V30, MAX_DISKNUM_V30, ALARMINFO_V30_ALARMTYPE_SEMAPHORE_ALARM, ALARMINFO_V30_ALARMTYPE_VIDEO_LOST, \
    ALARMINFO_V30_ALARMTYPE_TAMPERING_DETECTION, ALARMINFO_V30_ALARMTYPE_INTELLIGENT_SCENE_CHANGED
from .sdk.hcnetsdk import ALARMINFO_V30_ALARMTYPE_MOTION_DETECTION, BOOL, COMM_ALARM_V30, COMM_ALARM_VIDEO_INTERCOM, COMM_UPLOAD_VIDEO_INTERCOM_EVENT, DWORD, LONG, NET_DVR_ALARMER, NET_DVR_ALARMINFO_V30, NET_DVR_VIDEO_INTERCOM_ALARM, NET_DVR_VIDEO_INTERCOM_EVENT, NET_DVR_ALARM_ISAPI_INFO, NET_DVR_ACS_ALARM_INFO, COMM_ISAPI_ALARM, COMM_ALARM_ACS, MessageCallbackAlarmInfoUnion
from .sdk.acsalarminfo import AcsAlarmInfoMajor, AcsAlarmInfoMajorEvent
from .sdk.hcnetsdk import (
    VIDEO_INTERCOM_ALARM_ALARMTYPE_DISMISS_INCOMING_CALL,
    VIDEO_INTERCOM_ALARM_ALARMTYPE_DOORBELL_RINGING,
    VIDEO_INTERCOM_ALARM_ALARMTYPE_INTERCOM_ALARM,
)
from .sdk.utils import (
    SDKError,
    build_gate_alarm_anpr_event,
    build_its_plate_anpr_event,
    build_sdk_alarm_raw_event,
    build_upload_plate_anpr_event,
    build_vehicle_control_anpr_event,
    build_vehicle_control_list_dsalarm_event,
    call_ISAPI,
    format_struct_dump,
    is_ignored_sdk_alarm_type,
    is_ignored_vca_rule_event,
    map_vca_rule_event,
    read_anpr_image_from_plate_result,
    read_anpr_image_from_vehicle_control,
    read_sdk_alarm_image_buffer,
    sdk_ptz_control_other,
)
from .sdk.vehicle_control_list import (
    fetch_vehicle_control_list,
    fetch_vehicle_control_list_isapi,
    is_sdk_vehicle_list_unsupported,
)
from .sdk.video_intercom import (
    DEFAULT_CALLING_RING_SECONDS,
    DOORBELL_PULSE_SECONDS,
    INTERCOM_HANGUP_RELEASE_CMDS,
    INTERCOM_RECONNECT_BASE_DELAY,
    INTERCOM_RECONNECT_MAX_DELAY,
    SUBSCRIBE_ALARM_RECONNECT_BASE_DELAY,
    SUBSCRIBE_ALARM_RECONNECT_MAX_DELAY,
    IntercomCallState,
    VideoCallEvent,
    VideoCallCmdType,
    VideoIntercomRemoteConfig,
    intercom_sensors_for_state,
    next_intercom_call_state,
)

_LOGGER = logging.getLogger(__name__)

_KNOWN_SDK_COMM_COMMANDS = frozenset({
    COMM_ALARM,
    COMM_ALARM_V30,
    COMM_ALARM_ACS,
    COMM_ALARM_VIDEO_INTERCOM,
    COMM_UPLOAD_VIDEO_INTERCOM_EVENT,
    COMM_UPLOAD_FACESNAP_RESULT,
    COMM_ALARM_RULE,
    COMM_UPLOAD_PLATE_RESULT,
    COMM_ITS_PLATE_RESULT,
    COMM_VEHICLE_CONTROL_ALARM,
    COMM_VEHICLE_CONTROL_LIST_DSALARM,
    # COMM_ITS_GATE_ALARMINFO,
})


def _sdk_comm_command_name(command: int) -> str:
    """Resolve a COMM_* constant name for an SDK command code."""
    for name in dir(hcnetsdk):
        if name.startswith("COMM_") and getattr(hcnetsdk, name) == command:
            return name
    return f"0x{command:04x}"


def _sdk_device_log_prefix(device: HikvisionDevice) -> str:
    """Build [IP serial] prefix for SDK callback logs."""
    ip = device.device_info.ip_address or urlparse(device.host).hostname or "unknown"
    serial = device.device_info.serial_no or "unknown"
    return f"[{ip} {serial}]"


def _log_sdk_alarm_command(
    device: HikvisionDevice,
    command: int,
    alarm_info,
    buffer_length: int,
    *,
    known: bool,
) -> None:
    """Log SDK alarm payload; DEBUG dumps all commands, INFO only unknown ones."""
    s_cmd = _sdk_comm_command_name(command)
    source = _sdk_device_log_prefix(device)
    label = "SDK" if known else "Receive unknow cmd"

    try:
        payload = format_struct_dump(alarm_info)
    except Exception as err:  # pylint: disable=broad-except
        payload = f"(buffer_len={buffer_length}, struct_dump_failed={err})"

    if _LOGGER.isEnabledFor(logging.DEBUG):
        _LOGGER.debug("%s %s %s\n%s", label, source, s_cmd, payload)
    elif not known:
        _LOGGER.info("%s %s %s\n%s", label, source, s_cmd, payload)


def find_sdk_device(hass: HomeAssistant, alarmer: NET_DVR_ALARMER):
    """Match SDK alarm callback to the configured device instance."""
    sdk_ref: list[HikvisionDevice] = hass.data.get(DOMAIN, {}).get("sdk_ref", [])
    if not sdk_ref:
        return None

    if alarmer.byUserIDValid:
        for dev in sdk_ref:
            if dev.sdk_user == alarmer.lUserID:
                return dev

    if alarmer.byDeviceIPValid:
        device_ip = alarmer.sDeviceIP.decode("utf-8", errors="ignore").strip("\x00")
        for dev in sdk_ref:
            if dev.device_info.ip_address == device_ip or urlparse(dev.host).hostname == device_ip:
                return dev

    if alarmer.bySerialValid:
        serial = bytes(alarmer.sSerialNumber).split(b"\x00")[0].decode("utf-8", errors="ignore")
        for dev in sdk_ref:
            if dev.device_info.serial_no == serial:
                return dev

    _LOGGER.warning(
        "SDK callback: unable to match device (lUserID=%s, sdk_ref=%d)",
        alarmer.lUserID,
        len(sdk_ref),
    )
    return None


class HikvisionDevice(ISAPIClient):
    """Hikvision device for Home Assistant integration."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize device."""

        config = entry.data if entry else data
        self.entry = entry
        self.hass = hass
        self.auth_token_expired = False
        self.connection_type = resolve_connection_type(config)
        self.control_alarm_server_host = config[CONF_SET_ALARM_SERVER]
        self.alarm_server_host = config[CONF_ALARM_SERVER_HOST]

        # init ISAPI client
        host = config[CONF_HOST]
        username = config[CONF_USERNAME]
        password = config[CONF_PASSWORD]
        verify_ssl = config.get(CONF_VERIFY_SSL, True)
        rtsp_port_forced = config.get(RTSP_PORT_FORCED, None)
        session = get_async_client(hass, verify_ssl)
        super().__init__(host, username, password, verify_ssl, rtsp_port_forced, session, hass=hass)

        self.events_info: list[EventInfo] = []
        self.event_subscription: EventSubscription | None = None
        self.subscribe_coordinator: SubscribeStatusCoordinator | None = None
        self.intercom_coordinator: IntercomStatusCoordinator | None = None
        self.sdk_subscription: Any = None
        self.sdk_user: Any = None
        self.sdk_handle: LONG | None = None
        self.sdk_callback_func = None
        self._ptz_active: dict[int, dict[str, int]] = {}
        self._video_intercom: VideoIntercomRemoteConfig | None = None
        self._intercom_call_state = IntercomCallState.IDLE
        self._doorbell_off_unsub: Callable[[], None] | None = None
        self._calling_off_unsub: Callable[[], None] | None = None
        self._intercom_reconnect_unsub: Callable[[], None] | None = None
        self._intercom_reconnect_attempts = 0
        self._subscribe_reconnect_unsub: Callable[[], None] | None = None
        self._subscribe_reconnect_attempts = 0

    async def init_coordinators(self):
        """Initialize coordinators."""

        # init events supported by integration
        self.events_info = self.get_device_event_capabilities()
        for camera in self.cameras:
            camera.events_info = self.get_device_event_capabilities(camera.id)

        # create coordinators
        self.coordinators = {}
        self.coordinators[EVENTS_COORDINATOR] = EventsCoordinator(self.hass, self)
        if (
            self.capabilities.support_holiday_mode
            or self.capabilities.support_alarm_server
            or self.capabilities.storage
        ):
            self.coordinators[SECONDARY_COORDINATOR] = SecondaryCoordinator(self.hass, self)

        if self.control_alarm_server_host and self.capabilities.support_alarm_server:
            await self.set_alarm_server(self.alarm_server_host, ALARM_SERVER_PATH)

        self.subscribe_coordinator = SubscribeStatusCoordinator(self.hass, self)
        if self.capabilities.support_video_intercom and self.connection_type == CONF_CONNECTION_SDK:
            self.intercom_coordinator = IntercomStatusCoordinator(self.hass, self)

        if self.connection_type == CONF_CONNECTION_HTTP_NOTIFY:
            # Always bind to a stable wrapper method.
            # The wrapper will dynamically resolve the current notification_ctx at runtime.
            # This solves the problem where notification_ctx may be created after EventSubscription.
            # Subscription status coordinator (for the connectivity-style sensor)

            self.event_subscription = EventSubscription(
                self,
                on_event=self._dispatch_subscribed_event
            )

            # Use the new generic multi-event builder (respects SubscribeEventCap).
            # NVRs often expose ANPR only in subscribeEventCap, not ITCCap.support_anpr.
            cap = self.capabilities.subscribe_event_cap
            if "ANPR" in cap.supported_event_types or self.capabilities.support_anpr:
                event_types: list[str] | None = ["ANPR"]
            else:
                event_types = None
            try:
                await self.event_subscription.start(event_types=event_types)
            except Exception as ex:
                self.handle_exception(ex, "Failed to start event subscription (long-lived subscribeEvent)")
        elif self.connection_type == CONF_CONNECTION_SDK:
            device_info = NET_DVR_DEVICEINFO_V30()
            user_id = self.sdk_subscription.NET_DVR_Login_V30(
                urlparse(self.host).hostname.encode("utf-8"),
                8000,
                self.username.encode("utf-8"),
                self.password.encode("utf-8"),
                device_info,
            )
            if user_id < 0:
                _LOGGER.error(f"NET_DVR_Login_V30 failed, error code = {self.sdk_subscription.NET_DVR_GetLastError()}")
                raise SDKError(self.sdk_subscription, f"NET_DVR_Login_V30 failed")
            _LOGGER.info(f"Logged in with {user_id}")
            self.sdk_user = user_id

            if not self.hass.data[DOMAIN].get("sdk_callback_registered"):
                _LOGGER.debug("Registering global SDK callback function")
                self.sdk_callback_func = self._create_global_sdk_callback()
                self.hass.data[DOMAIN]["sdk_callback"] = self.sdk_callback_func
                result = self.sdk_subscription.NET_DVR_SetDVRMessageCallBack_V50(
                    0,
                    self.sdk_callback_func,
                    None,
                )
                if not result:
                    raise SDKError(self.sdk_subscription, "Error while setting up event manager")
                self.hass.data[DOMAIN]["sdk_callback_registered"] = True

            await self._setup_sdk_alarm_channel()

            if self.capabilities.support_video_intercom:
                await self._start_video_intercom_remote_config()

        # first data fetch
        for coordinator in self.coordinators.values():
            await coordinator.async_config_entry_first_refresh()

    def _handle_callback(self, command: int, alarm_device_pointer, alarm_info_pointer, buffer_length, user_pointer):
        # _LOGGER.debug("Callback invoked from SDK")
        device: NET_DVR_ALARMER = alarm_device_pointer.contents

        # Cast the alarm_info pointer to the correct Python class
        alarm_info = self._cast_alarm_info(command, alarm_info_pointer)

        # Invoke the registered handlers on the main asyncio loop
        future = asyncio.run_coroutine_threadsafe(
            self._invoke_handlers(
                command, device, alarm_info, buffer_length, user_pointer),
            self.hass.loop)
        future.result()

    async def _invoke_handlers(self, command, device: NET_DVR_ALARMER, alarm_info, buffer_length, user_pointer):
        # Select the handler function to call based on the type of alarm we have received
        raw_event = None

        known_command = command in _KNOWN_SDK_COMM_COMMANDS
        if _LOGGER.isEnabledFor(logging.DEBUG) or not known_command:
            _log_sdk_alarm_command(
                self,
                command,
                alarm_info,
                buffer_length,
                known=known_command,
            )

        # COMM_CROSSLINE_ALARM

        match alarm_info:
            case NET_DVR_ALARMINFO_V30():
                channel_bitmap = list(alarm_info.byChannel)
                channel_id = channel_from_bitmap(channel_bitmap)
                raw_event = build_sdk_alarm_raw_event(
                    alarm_info.dwAlarmType,
                    channel_id=channel_id,
                    io_port_id=alarm_info.dwAlarmInputNumber,
                )
                if raw_event is None and not is_ignored_sdk_alarm_type(alarm_info.dwAlarmType):
                    _LOGGER.info(
                        "Unhandled SDK alarm type %s on %s (COMM_ALARM_V30)",
                        alarm_info.dwAlarmType,
                        self.device_info.serial_no,
                    )
                else:
                    raw_event.update({
                        "AlarmOutputNumber": list(alarm_info.byAlarmOutputNumber),
                        "AlarmRelateChannel": list(alarm_info.byAlarmRelateChannel),
                        "channels": channel_bitmap,
                        "diskNumbers": list(alarm_info.byDiskNumber),
                    })
            case NET_DVR_VIDEO_INTERCOM_ALARM():
                await self._handle_video_intercom_alarm(alarm_info)
            case NET_DVR_VIDEO_INTERCOM_EVENT():
                await self._handle_video_intercom_event(alarm_info)
            case NET_DVR_ALARM_ISAPI_INFO():
                pass
            case NET_DVR_VEHICLE_CONTROL_ALARM():
                raw_event = build_vehicle_control_anpr_event(alarm_info)
                if raw_event is None:
                    _LOGGER.debug(
                        "Vehicle control alarm on channel %s has no license plate",
                        alarm_info.dwChannel,
                    )
                elif alarm_info.dwPicDataLen and alarm_info.byPicTransType == 1:
                    _LOGGER.debug(
                        "Vehicle control ANPR on channel %s uses URL image upload; "
                        "picture bytes not included in SDK callback",
                        alarm_info.dwChannel,
                    )
                elif alarm_info.dwPicDataLen:
                    image_bytes = read_anpr_image_from_vehicle_control(alarm_info)
                    if image_bytes:
                        ctx = self.hass.data.get(DOMAIN, {}).get("notification_ctx")
                        if ctx:
                            await ctx.update_anpr_snap_image(
                                self,
                                raw_event["channelID"],
                                image_bytes,
                                license_plate=raw_event.get("ANPR", {}).get("licensePlate"),
                            )
            case NET_DVR_ACS_ALARM_INFO():
                if (
                    alarm_info.dwMajor == AcsAlarmInfoMajor.MAJOR_EVENT.value
                    and alarm_info.dwMinor
                    == AcsAlarmInfoMajorEvent.MINOR_DOORBELL_RINGING.value
                ):
                    _LOGGER.info(
                        "Doorbell ringing via COMM_ALARM_ACS on %s",
                        self.device_info.serial_no,
                    )
                    await self._pulse_doorbell(source="acs_alarm", call_event=None)
                    return
                from .sdk.acs_event import build_acs_access_controller_event
                from .sdk.acsalarminfo import ACS_FACE_VERIFY_PASS_MINORS
                from .sdk.utils import read_acs_face_image_from_alarm

                raw_event = build_acs_access_controller_event(alarm_info)
                if (
                    alarm_info.dwMajor == AcsAlarmInfoMajor.MAJOR_EVENT.value
                    and alarm_info.dwMinor in ACS_FACE_VERIFY_PASS_MINORS
                    and alarm_info.dwPicDataLen
                ):
                    if getattr(alarm_info, "byPicTransType", 0) == 1:
                        _LOGGER.debug(
                            "ACS face verify on %s uses URL image upload; "
                            "picture bytes not included in SDK callback",
                            self.device_info.serial_no,
                        )
                    else:
                        image_bytes = read_acs_face_image_from_alarm(alarm_info)
                        if image_bytes:
                            ctx = self.hass.data.get(DOMAIN, {}).get("notification_ctx")
                            if ctx:
                                ace = raw_event.get("AccessControllerEvent", {})
                                await ctx.update_face_verify_image(
                                    self,
                                    image_bytes,
                                    person_name=ace.get("name"),
                                    employee_no=ace.get("employeeNoString"),
                                    card_no=ace.get("cardNo"),
                                    pic_len=alarm_info.dwPicDataLen,
                                )
                        else:
                            _LOGGER.debug(
                                "ACS face verify on %s has pic len %s but no readable bytes",
                                self.device_info.serial_no,
                                alarm_info.dwPicDataLen,
                            )
            case NET_DVR_ALARMINFO():
                raw_event = build_sdk_alarm_raw_event(
                    alarm_info.dwAlarmType,
                    channel_id=alarm_info.dwChannel,
                    io_port_id=alarm_info.dwAlarmInputNumber,
                    alarm_output_number=alarm_info.dwAlarmOutputNumber,
                    relate_channel_id=alarm_info.dwAlarmRelateChannel,
                    disk_number=alarm_info.dwDiskNumber,
                )
                if raw_event is None and not is_ignored_sdk_alarm_type(alarm_info.dwAlarmType):
                    _LOGGER.info(
                        "Unhandled SDK alarm type %s on %s (COMM_ALARM)",
                        alarm_info.dwAlarmType,
                        self.device_info.serial_no,
                    )
            case NET_DVR_ALARMINFO_V40():
                pass
            case NET_VCA_RULE_ALARM():
                rule_info = alarm_info.struRuleInfo
                event_id = map_vca_rule_event(rule_info.wEventTypeEx, rule_info.dwEventType)
                if not event_id:
                    if not is_ignored_vca_rule_event(
                        rule_info.wEventTypeEx, rule_info.dwEventType
                    ):
                        _LOGGER.info(
                            "Unhandled VCA rule event wEventTypeEx=%s dwEventType=%s",
                            rule_info.wEventTypeEx,
                            rule_info.dwEventType,
                        )
                    return
                channel_id = (
                    alarm_info.wDevInfoIvmsChannelEx
                    or alarm_info.struDevInfo.byIvmsChannel
                    or alarm_info.struDevInfo.byChannel
                    or 0
                )
                ctx = self.hass.data.get(DOMAIN, {}).get("notification_ctx")
                if ctx:
                    alert = AlertInfo(
                        channel_id=channel_id,
                        io_port_id=0,
                        event_id=event_id,
                        region_id=rule_info.byRuleID,
                    )
                    ctx.device = self
                    ctx.update_alert_channel(alert)
                    try:
                        ctx.trigger_sensor(alert)
                    except ValueError:
                        _LOGGER.warning(
                            "No binary sensor for VCA rule event %s on channel %s",
                            event_id,
                            channel_id,
                        )
            case NET_VCA_FACESNAP_RESULT():
                channel_id = (
                    alarm_info.wDevInfoIvmsChannelEx
                    or alarm_info.struDevInfo.byIvmsChannel
                    or 0
                )
                upload_type = alarm_info.byUploadEventDataType
                face_bytes = read_sdk_alarm_image_buffer(
                    alarm_info.pBuffer1,
                    alarm_info.dwFacePicLen,
                    upload_type,
                )
                background_bytes = read_sdk_alarm_image_buffer(
                    alarm_info.pBuffer2,
                    alarm_info.dwBackgroundPicLen,
                    upload_type,
                )
                image_bytes = face_bytes or background_bytes
                if upload_type == 1 and (alarm_info.dwFacePicLen or alarm_info.dwBackgroundPicLen):
                    _LOGGER.debug(
                        "Face snap on channel %s uses URL upload mode; image bytes not in callback",
                        channel_id,
                    )
                elif image_bytes and self.hass.data.get(DOMAIN, {}).get("notification_ctx"):
                    ctx = self.hass.data[DOMAIN]["notification_ctx"]
                    await ctx.update_face_snap_image(
                        self,
                        channel_id,
                        image_bytes,
                        face_pic_id=alarm_info.dwFacePicID,
                        face_score=alarm_info.dwFaceScore,
                    )
            case NET_VCA_FACESNAP_MATCH_ALARM():
                pass
            case NET_DVR_VEHICLE_CONTROL_LIST_DSALARM():
                # Ignore Control List Disalarm event
                # await self._handle_vehicle_control_list_dsalarm(alarm_info)
                pass
            case NET_ITS_PLATE_RESULT():
                raw_event = build_its_plate_anpr_event(alarm_info)
            case NET_DVR_PLATE_RESULT():
                raw_event = build_upload_plate_anpr_event(alarm_info)
                if raw_event:
                    image_bytes = read_anpr_image_from_plate_result(alarm_info)
                    if image_bytes:
                        ctx = self.hass.data.get(DOMAIN, {}).get("notification_ctx")
                        if ctx:
                            await ctx.update_anpr_snap_image(
                                self,
                                raw_event["channelID"],
                                image_bytes,
                                license_plate=raw_event.get("ANPR", {}).get("licensePlate"),
                                scene_len=alarm_info.dwPicLen or None,
                                plate_len=alarm_info.dwPicPlateLen or None,
                            )
                    elif alarm_info.dwPicLen or alarm_info.dwPicPlateLen:
                        _LOGGER.debug(
                            "Upload plate ANPR on channel %s has pic lengths "
                            "scene=%s plate=%s but no readable image bytes",
                            raw_event.get("channelID"),
                            alarm_info.dwPicLen,
                            alarm_info.dwPicPlateLen,
                        )
            case NET_DVR_GATE_ALARMINFO():
                raw_event = build_gate_alarm_anpr_event(alarm_info)
                if raw_event is None:
                    _LOGGER.debug(
                        "Unhandled gate alarm type 0x%02x (externalDevType=%s)",
                        alarm_info.byAlarmType,
                        alarm_info.byExternalDevType,
                    )
            case None:
                pass
            case _:
                print("Unknow alarm -> ", alarm_info)
                pass

        if raw_event is not None and self.hass.data.get(DOMAIN) and self.hass.data[DOMAIN].get("notification_ctx"):
            raw_event.update({
                "serial": self.device_info.serial_no,
                "ipAddress": self.device_info.ip_address,
                "macAddress": self.device_info.mac_address,
            })
            ctx = self.hass.data[DOMAIN]["notification_ctx"]
            ctx.device = self
            await ctx.handle_subscribed_event(raw_event, device=self)

    def _cast_alarm_info(self, command: int, callback_alarm_info_p):
        '''Cast the alarm_info pointer received from the callback to the correct Python class, depending on the value of `command`'''
        if command == COMM_ALARM_V30:
            return cast(callback_alarm_info_p, POINTER(NET_DVR_ALARMINFO_V30)).contents
        elif command == COMM_ALARM_VIDEO_INTERCOM:
            return cast(callback_alarm_info_p, POINTER(NET_DVR_VIDEO_INTERCOM_ALARM)).contents
        elif command == COMM_UPLOAD_VIDEO_INTERCOM_EVENT:
            return cast(callback_alarm_info_p, POINTER(NET_DVR_VIDEO_INTERCOM_EVENT)).contents
        elif command == COMM_ISAPI_ALARM:
            return cast(callback_alarm_info_p, POINTER(NET_DVR_ALARM_ISAPI_INFO)).contents
        elif command == COMM_ALARM_ACS:
            return cast(callback_alarm_info_p, POINTER(NET_DVR_ACS_ALARM_INFO)).contents
        elif command == COMM_VEHICLE_CONTROL_ALARM:
            return cast(callback_alarm_info_p, POINTER(NET_DVR_VEHICLE_CONTROL_ALARM)).contents
        elif command == COMM_ALARM:
            return cast(callback_alarm_info_p, POINTER(NET_DVR_ALARMINFO)).contents
        elif command == COMM_ALARM_V40:
            return cast(callback_alarm_info_p, POINTER(NET_DVR_ALARMINFO_V40)).contents
        elif command == COMM_ALARM_RULE:
            return cast(callback_alarm_info_p, POINTER(NET_VCA_RULE_ALARM)).contents
        elif command == COMM_UPLOAD_FACESNAP_RESULT:
            return cast(callback_alarm_info_p, POINTER(NET_VCA_FACESNAP_RESULT)).contents
        elif command == COMM_SNAP_MATCH_ALARM:
            return cast(callback_alarm_info_p, POINTER(NET_VCA_FACESNAP_MATCH_ALARM)).contents
        elif command == COMM_VEHICLE_CONTROL_LIST_DSALARM:
            return cast(callback_alarm_info_p, POINTER(NET_DVR_VEHICLE_CONTROL_LIST_DSALARM)).contents
        elif command == COMM_UPLOAD_PLATE_RESULT:
            return cast(callback_alarm_info_p, POINTER(NET_DVR_PLATE_RESULT)).contents
        elif command == COMM_ITS_PLATE_RESULT:
            return cast(callback_alarm_info_p, POINTER(NET_ITS_PLATE_RESULT)).contents
        elif command == COMM_ITS_GATE_ALARMINFO:
            return cast(callback_alarm_info_p, POINTER(NET_DVR_GATE_ALARMINFO)).contents
        else:
            _LOGGER.debug("Received unhandled command: 0x%04x (%d)", command, command)
            return None

    def _create_global_sdk_callback(self):
        """Create a single SDK callback shared by all SDK-mode config entries."""
        hass = self.hass

        @CFUNCTYPE(BOOL, LONG, POINTER(NET_DVR_ALARMER), POINTER(MessageCallbackAlarmInfoUnion), DWORD, c_void_p)
        def callback(command: int, alarm_device_pointer, alarm_info_pointer, buffer_length, user_pointer):
            alarmer = alarm_device_pointer.contents
            device = find_sdk_device(hass, alarmer)
            if device is None:
                return True
            device._handle_callback(
                command,
                alarm_device_pointer,
                alarm_info_pointer,
                buffer_length,
                user_pointer,
            )
            return True

        return callback


    def sdk_isapi(self, method, inUrl, inPutBuffer):
        return call_ISAPI(self.sdk_subscription, self.sdk_user, method, inUrl, inPutBuffer)

    def _sdk_ptz_control(
        self,
        channel: int,
        command: int,
        stop: bool,
        *,
        speed: int = 4,
    ) -> None:
        """Run SDK PTZ control on the executor thread."""
        sdk_ptz_control_other(
            self.sdk_subscription,
            self.sdk_user,
            channel,
            command,
            stop,
            speed=speed,
        )

    async def _sdk_ptz_try_channels(
        self,
        camera: AnalogCamera | IPCamera,
        command: int,
        *,
        stop: bool,
    ) -> int:
        """Try SDK PTZ on candidate channel numbers until one succeeds."""
        last_error: SDKError | None = None
        for channel in ptz_sdk_channel_candidates(camera, is_nvr=self.device_info.is_nvr):
            try:
                await self.hass.async_add_executor_job(
                    self._sdk_ptz_control,
                    channel,
                    command,
                    stop,
                )
                return channel
            except SDKError as ex:
                last_error = ex
                _LOGGER.debug("SDK PTZ failed on channel %s: %s", channel, ex.args)
        if last_error:
            raise last_error
        raise SDKError(self.sdk_subscription, f"PTZ command {command} failed on channel {camera.id}")

    async def ptz_start(
        self,
        camera: AnalogCamera | IPCamera,
        *,
        action: str | None = None,
        pan: int = 0,
        tilt: int = 0,
        zoom: int = 0,
    ) -> None:
        """Start continuous PTZ movement (ISAPI or SDK native API)."""
        if not camera.support_ptz:
            raise ValueError(f"Camera channel {camera.id} does not support PTZ")

        if self.connection_type == CONF_CONNECTION_SDK and ptz_prefers_isapi(
            camera, is_nvr=self.device_info.is_nvr
        ):
            _LOGGER.debug("PTZ start via ISAPI proxy for NVR channel %s", camera.id)
            await self.ptz_continuous(camera, pan=pan, tilt=tilt, zoom=zoom)
            if action:
                command = ptz_command_from_action(action)
            else:
                command = ptz_command_from_velocities(pan, tilt, zoom)
            if command is not None:
                self._ptz_active[camera.id] = {"channel": camera.id, "command": command}
            return

        if self.connection_type == CONF_CONNECTION_SDK:
            command = ptz_command_from_action(action) if action else ptz_command_from_velocities(pan, tilt, zoom)
            if command is None:
                raise ValueError("No PTZ direction specified")
            channel = await self._sdk_ptz_try_channels(camera, command, stop=False)
            self._ptz_active[camera.id] = {"channel": channel, "command": command}
            _LOGGER.info("SDK PTZ start channel=%s command=%s", channel, command)
            return

        await self.ptz_continuous(camera, pan=pan, tilt=tilt, zoom=zoom)

    async def ptz_stop(self, camera: AnalogCamera | IPCamera, action: str | None = None) -> None:
        """Stop PTZ movement."""
        if not camera.support_ptz:
            raise ValueError(f"Camera channel {camera.id} does not support PTZ")

        if self.connection_type == CONF_CONNECTION_SDK and ptz_prefers_isapi(
            camera, is_nvr=self.device_info.is_nvr
        ):
            _LOGGER.debug("PTZ stop via ISAPI proxy for NVR channel %s", camera.id)
            await super().ptz_stop(camera)
            self._ptz_active.pop(camera.id, None)
            return

        if self.connection_type == CONF_CONNECTION_SDK:
            active = self._ptz_active.get(camera.id, {})
            command = ptz_command_from_action(action) if action else active.get("command")
            if command is None:
                return
            channel = active.get("channel")
            if channel is None:
                channel = await self._sdk_ptz_try_channels(camera, command, stop=True)
            else:
                await self.hass.async_add_executor_job(
                    self._sdk_ptz_control,
                    channel,
                    command,
                    True,
                )
            if self._ptz_active.get(camera.id, {}).get("command") == command:
                self._ptz_active.pop(camera.id, None)
            _LOGGER.info("SDK PTZ stop channel=%s command=%s", channel, command)
            return

        await super().ptz_stop(camera)

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

    def _sdk_alarm_error_message(self, errno: int) -> str:
        return (
            f"{errno}: "
            f"{self.sdk_subscription.NET_DVR_GetErrorMsg(c_long(errno)).decode('utf-8')}"
        )

    def _build_sdk_alarm_param(self) -> NET_DVR_SETUPALARM_PARAM_V50:
        alarm_param = NET_DVR_SETUPALARM_PARAM_V50()
        alarm_param.dwSize = sizeof(NET_DVR_SETUPALARM_PARAM_V50)
        alarm_param.byLevel = 2
        alarm_param.byAlarmInfoType = 1
        alarm_param.byFaceAlarmDetection = 1
        alarm_param.byDeployType = 1
        # bit0: license plate (IPC/ITS), bit3: face snap, bit4: face contrast (IPC)
        alarm_param.bySupport = alarm_param.bySupport | 0x19
        # bit3: subscribe to COMM_ALARM_RULE (VCA behavior detection)
        alarm_param.byBrokenNetHttpV60 = alarm_param.byBrokenNetHttpV60 | 0x08
        # This flips bit 1 to 0, telling the doorbell NOT to send the backlog.
        alarm_param.bySupport = alarm_param.bySupport & ~0x02
        return alarm_param

    def _teardown_sdk_alarm_channel(self) -> None:
        """Close the SDK alarm channel handle if armed."""
        if self.sdk_handle is None or self.sdk_subscription is None:
            return
        handle = self.sdk_handle
        self.sdk_handle = None
        self.sdk_subscription.NET_DVR_CloseAlarmChan_V30(handle)

    async def _setup_sdk_alarm_channel(self) -> bool:
        """Arm SDK alarm channel; returns True on success."""
        if self.sdk_user is None or self.sdk_subscription is None:
            return False

        self._teardown_sdk_alarm_channel()

        _LOGGER.debug("Arming the device via SDK")
        alarm_param = self._build_sdk_alarm_param()
        alarm_handle = self.sdk_subscription.NET_DVR_SetupAlarmChan_V50(
            self.sdk_user,
            alarm_param,
            None,
            0,
        )
        if alarm_handle < 0:
            errno = self.sdk_subscription.NET_DVR_GetLastError()
            reason = self._sdk_alarm_error_message(errno)
            _LOGGER.error("Error while listening to events, %s", reason)
            await self.async_set_subscribe_connected(False, reason)
            return False

        self.sdk_handle = alarm_handle
        await self.async_set_subscribe_connected(True)
        _LOGGER.info(
            "SDK alarm channel armed for %s (handle=%s)",
            self.device_info.serial_no,
            alarm_handle,
        )
        return True

    def _should_reconnect_subscribe(self) -> bool:
        return (
            self.connection_type == CONF_CONNECTION_SDK
            and self.sdk_user is not None
            and self.sdk_subscription is not None
            and self.subscribe_coordinator is not None
        )

    def _cancel_subscribe_reconnect(self) -> None:
        if self._subscribe_reconnect_unsub is not None:
            self._subscribe_reconnect_unsub()
            self._subscribe_reconnect_unsub = None

    def _schedule_subscribe_reconnect(self, reason: str | None = None) -> None:
        """Retry NET_DVR_SetupAlarmChan_V50 after a failed or dropped alarm channel."""
        if not self._should_reconnect_subscribe():
            return
        if self._subscribe_reconnect_unsub is not None:
            return

        delay = min(
            SUBSCRIBE_ALARM_RECONNECT_BASE_DELAY * (2 ** self._subscribe_reconnect_attempts),
            SUBSCRIBE_ALARM_RECONNECT_MAX_DELAY,
        )
        self._subscribe_reconnect_attempts += 1
        _LOGGER.info(
            "Scheduling SDK alarm channel reconnect for %s in %ss (%s)",
            self.device_info.serial_no,
            delay,
            reason or "disconnected",
        )

        def _run_reconnect(_now) -> None:
            self._subscribe_reconnect_unsub = None
            asyncio.run_coroutine_threadsafe(
                self._reconnect_sdk_alarm_channel(),
                self.hass.loop,
            )

        self._subscribe_reconnect_unsub = async_call_later(self.hass, delay, _run_reconnect)

    async def _reconnect_sdk_alarm_channel(self) -> None:
        """Restart the SDK alarm channel after SetupAlarmChan_V50 failure."""
        if not self._should_reconnect_subscribe():
            return
        _LOGGER.info(
            "Reconnecting SDK alarm channel for %s",
            self.device_info.serial_no,
        )
        await self._setup_sdk_alarm_channel()

    def _should_reconnect_intercom(self) -> bool:
        return (
            self.connection_type == CONF_CONNECTION_SDK
            and self.capabilities.support_video_intercom
            and self.sdk_user is not None
            and self.intercom_coordinator is not None
        )

    def _cancel_intercom_reconnect(self) -> None:
        if self._intercom_reconnect_unsub is not None:
            self._intercom_reconnect_unsub()
            self._intercom_reconnect_unsub = None

    def _schedule_intercom_reconnect(self, reason: str | None = None) -> None:
        """Retry VideoIntercomRemoteConfig after the SDK session drops."""
        if not self._should_reconnect_intercom():
            return
        if self._intercom_reconnect_unsub is not None:
            return

        delay = min(
            INTERCOM_RECONNECT_BASE_DELAY * (2 ** self._intercom_reconnect_attempts),
            INTERCOM_RECONNECT_MAX_DELAY,
        )
        self._intercom_reconnect_attempts += 1
        _LOGGER.info(
            "Scheduling intercom RemoteConfig reconnect for %s in %ss (%s)",
            self.device_info.serial_no,
            delay,
            reason or "disconnected",
        )

        def _run_reconnect(_now) -> None:
            self._intercom_reconnect_unsub = None
            asyncio.run_coroutine_threadsafe(
                self._reconnect_video_intercom_remote_config(),
                self.hass.loop,
            )

        self._intercom_reconnect_unsub = async_call_later(self.hass, delay, _run_reconnect)

    async def _teardown_video_intercom_session(self) -> None:
        """Stop the SDK RemoteConfig session without marking a permanent shutdown."""
        session = self._video_intercom
        if session is None:
            return
        await self.hass.async_add_executor_job(session.stop)
        self._video_intercom = None

    async def _reconnect_video_intercom_remote_config(self) -> None:
        """Restart the video intercom RemoteConfig listener after a disconnect."""
        if not self._should_reconnect_intercom():
            return
        _LOGGER.info(
            "Reconnecting video intercom RemoteConfig for %s",
            self.device_info.serial_no,
        )
        await self._teardown_video_intercom_session()
        await self._start_video_intercom_remote_config()

    async def _start_video_intercom_remote_config(self) -> None:
        """Subscribe to call signals via NET_DVR_StartRemoteConfig."""
        if self.connection_type != CONF_CONNECTION_SDK or self.sdk_user is None:
            return
        if self._video_intercom is not None:
            await self._teardown_video_intercom_session()

        def on_call_event(event: VideoCallEvent) -> None:
            asyncio.run_coroutine_threadsafe(
                self._handle_video_call_event(event),
                self.hass.loop,
            )

        def on_status_change(connected: bool, reason: str | None = None) -> None:
            asyncio.run_coroutine_threadsafe(
                self.async_set_intercom_connected(connected, reason),
                self.hass.loop,
            )

        session = VideoIntercomRemoteConfig(
            self.sdk_subscription,
            self.sdk_user,
            on_call_event,
            on_status_change=on_status_change,
        )

        try:
            await self.hass.async_add_executor_job(session.start)
        except SDKError as ex:
            _LOGGER.error("Failed to start video intercom RemoteConfig: %s", ex)
            await self.async_set_intercom_connected(False, str(ex))
            return

        self._video_intercom = session
        await self.async_set_intercom_connected(True)
        _LOGGER.info("Video intercom call signal listener active")

    async def _stop_video_intercom_remote_config(self) -> None:
        """Stop NET_DVR_StopRemoteConfig session."""
        self._cancel_intercom_reconnect()
        session = self._video_intercom
        if session is None:
            await self.async_set_intercom_connected(False, "stopped")
            return
        await self.hass.async_add_executor_job(session.stop)
        self._video_intercom = None
        self._intercom_call_state = IntercomCallState.IDLE
        await self.async_set_intercom_call_state(IntercomCallState.IDLE)
        self._cancel_doorbell_pulse()
        self._cancel_calling_timeout()
        await self._trigger_doorbell(False, source="stopped")
        await self._trigger_calling(False, source="stopped")
        await self._trigger_intercom(False, source="stopped")
        await self.async_set_intercom_connected(False, "stopped")

    async def _handle_video_call_event(self, event: VideoCallEvent) -> None:
        """Handle NET_DVR_VIDEO_CALL_PARAM events from RemoteConfig."""
        _LOGGER.info(
            "Video intercom call signal on %s: cmd=%s building=%s unit=%s room=%s dev_index=%s",
            self.device_info.serial_no,
            event.cmd_type,
            event.building_number,
            event.unit_number,
            event.room_number,
            event.dev_index,
        )
        if event.cmd_type == VideoCallCmdType.END_CALL:
            try:
                await self._send_intercom_command(
                    VideoCallCmdType.END_CALL,
                    apply_state=False,
                )
            except Exception:  # pylint: disable=broad-except
                _LOGGER.warning(
                    "Failed to acknowledge remote END_CALL on %s",
                    self.device_info.serial_no,
                    exc_info=True,
                )
        await self._apply_intercom_call_cmd(
            event.cmd_type,
            source="remote_config",
            call_event=event,
        )

    async def _apply_intercom_call_cmd(
        self,
        cmd_type: int,
        *,
        source: str,
        call_event: VideoCallEvent | None = None,
    ) -> None:
        """Update doorbell/calling/in-call sensors from a video intercom command."""
        new_state = next_intercom_call_state(cmd_type, self._intercom_call_state)
        if new_state is None:
            return
        prev_state = self._intercom_call_state
        self._intercom_call_state = new_state
        calling_active, in_call_active = intercom_sensors_for_state(new_state)
        if new_state == IntercomCallState.RINGING:
            self._schedule_calling_timeout()
        else:
            self._cancel_calling_timeout()
        await self._trigger_calling(calling_active, source=source, call_event=call_event)
        await self._trigger_intercom(in_call_active, source=source, call_event=call_event)
        await self.async_set_intercom_call_state(new_state)

    async def _fetch_vehicle_control_list_entries(
        self, data_index: int
    ) -> tuple[list[dict], str]:
        """Fetch allow/block list entries, preferring SDK and falling back to ISAPI."""
        if self.sdk_user is not None and self.sdk_subscription is not None:
            try:
                entries = await self.hass.async_add_executor_job(
                    fetch_vehicle_control_list,
                    self.sdk_subscription,
                    self.sdk_user,
                    data_index,
                )
                return entries, "sdk"
            except SDKError as ex:
                if is_sdk_vehicle_list_unsupported(ex):
                    _LOGGER.debug(
                        "SDK vehicle control list fetch unsupported on %s (data_index=%s): %s",
                        self.device_info.serial_no,
                        data_index,
                        ex,
                    )
                else:
                    _LOGGER.warning(
                        "Failed to fetch vehicle control list on %s from data_index=%s: %s",
                        self.device_info.serial_no,
                        data_index,
                        ex,
                    )
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception(
                    "Vehicle control list SDK fetch failed on %s (data_index=%s)",
                    self.device_info.serial_no,
                    data_index,
                )

        try:
            entries = await fetch_vehicle_control_list_isapi(self)
            return entries, "isapi"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug(
                "ISAPI vehicle control list fetch failed on %s (data_index=%s)",
                self.device_info.serial_no,
                data_index,
                exc_info=True,
            )
        return [], "none"

    async def _handle_vehicle_control_list_dsalarm(
        self, alarm_info: NET_DVR_VEHICLE_CONTROL_LIST_DSALARM
    ) -> None:
        """Handle COMM_VEHICLE_CONTROL_LIST_DSALARM (blocklist/allowlist sync notification)."""
        payload = build_vehicle_control_list_dsalarm_event(alarm_info)
        entries, fetch_source = await self._fetch_vehicle_control_list_entries(
            alarm_info.dwDataIndex
        )
        payload["entries"] = entries
        payload["entry_count"] = len(entries)
        payload["fetch_source"] = fetch_source
        _LOGGER.info(
            "Vehicle control list sync on %s: data_index=%s operate_index=%s entries=%s source=%s",
            self.device_info.serial_no,
            payload["data_index"],
            payload["operate_index"] or "(empty)",
            len(entries),
            fetch_source,
        )
        self.hass.bus.fire(
            HIKVISION_EVENT,
            {
                "channel_id": 0,
                "io_port_id": 0,
                "camera_name": "",
                **payload,
            },
        )

    async def _handle_video_intercom_alarm(self, alarm_info: NET_DVR_VIDEO_INTERCOM_ALARM) -> None:
        """Handle COMM_ALARM_VIDEO_INTERCOM callback."""
        alarm_type = alarm_info.byAlarmType
        dev_number = bytes(alarm_info.byDevNumber).split(b"\x00")[0]
        _LOGGER.info(
            "Video intercom alarm on %s: type=%s dev=%s",
            self.device_info.serial_no,
            alarm_type,
            dev_number,
        )
        if alarm_type in (
            VIDEO_INTERCOM_ALARM_ALARMTYPE_DOORBELL_RINGING,
            VIDEO_INTERCOM_ALARM_ALARMTYPE_INTERCOM_ALARM,
        ):
            await self._apply_intercom_call_cmd(
                VideoCallCmdType.CALLING,
                source="alarm",
            )
        elif alarm_type == VIDEO_INTERCOM_ALARM_ALARMTYPE_DISMISS_INCOMING_CALL:
            await self._apply_intercom_call_cmd(
                VideoCallCmdType.CANCEL_CALL,
                source="alarm",
            )

    async def _handle_video_intercom_event(self, alarm_info: NET_DVR_VIDEO_INTERCOM_EVENT) -> None:
        """Handle COMM_UPLOAD_VIDEO_INTERCOM_EVENT callback (logged for now)."""
        _LOGGER.debug(
            "Video intercom event type %s from device %s",
            alarm_info.byEventType,
            bytes(alarm_info.byDevNumber).split(b"\x00")[0],
        )

    def _cancel_doorbell_pulse(self) -> None:
        if self._doorbell_off_unsub is not None:
            self._doorbell_off_unsub()
            self._doorbell_off_unsub = None

    def _cancel_calling_timeout(self) -> None:
        if self._calling_off_unsub is not None:
            self._calling_off_unsub()
            self._calling_off_unsub = None

    def _schedule_calling_timeout(
        self,
        delay: float = DEFAULT_CALLING_RING_SECONDS,
    ) -> None:
        """Auto-clear calling when the device omits CANCEL/END/TIMEOUT callbacks."""
        self._cancel_calling_timeout()

        def _end_calling(_now) -> None:
            self._calling_off_unsub = None
            asyncio.run_coroutine_threadsafe(
                self._clear_calling_after_timeout(),
                self.hass.loop,
            )

        self._calling_off_unsub = async_call_later(self.hass, delay, _end_calling)

    async def _clear_calling_after_timeout(self) -> None:
        if self._intercom_call_state != IntercomCallState.RINGING:
            return
        _LOGGER.info(
            "Calling ring timeout on %s; clearing calling state",
            self.device_info.serial_no,
        )
        self._intercom_call_state = IntercomCallState.IDLE
        await self.async_set_intercom_call_state(IntercomCallState.IDLE)
        await self._trigger_calling(False, source="timeout")
        await self._trigger_intercom(False, source="timeout")

    async def _pulse_doorbell(
        self,
        *,
        source: str,
        call_event: VideoCallEvent | None = None,
    ) -> None:
        """Momentarily set doorbell on, then auto-off after a short pulse."""
        self._cancel_doorbell_pulse()
        await self._trigger_doorbell(True, source=source, call_event=call_event)

        def _end_pulse(_now) -> None:
            self._doorbell_off_unsub = None
            asyncio.run_coroutine_threadsafe(
                self._trigger_doorbell(False, source="pulse"),
                self.hass.loop,
            )

        self._doorbell_off_unsub = async_call_later(
            self.hass,
            DOORBELL_PULSE_SECONDS,
            _end_pulse,
        )

    async def _trigger_doorbell(
        self,
        active: bool,
        *,
        source: str,
        call_event: VideoCallEvent | None = None,
        alarm_type: int | None = None,
    ) -> None:
        """Fire the doorbell binary sensor and HASS event."""
        ctx = self.hass.data.get(DOMAIN, {}).get("notification_ctx")
        if not ctx:
            return

        from homeassistant.const import STATE_OFF, STATE_ON

        alert = AlertInfo(
            channel_id=0,
            io_port_id=0,
            event_id="doorbell",
            state=STATE_ON if active else STATE_OFF,
        )
        ctx.device = self
        ctx.update_alert_channel(alert)
        try:
            ctx.trigger_sensor(alert)
        except ValueError:
            _LOGGER.debug("No doorbell binary sensor registered for %s", self.device_info.serial_no)

        message = {
            "channel_id": 0,
            "io_port_id": 0,
            "camera_name": "",
            "event_id": "doorbell",
            "source": source,
            "active": active,
        }
        if call_event is not None:
            message.update(
                {
                    "cmd_type": call_event.cmd_type,
                    "building_number": call_event.building_number,
                    "unit_number": call_event.unit_number,
                    "room_number": call_event.room_number,
                    "dev_index": call_event.dev_index,
                }
            )
        if alarm_type is not None:
            message["alarm_type"] = alarm_type
        self.hass.bus.fire(HIKVISION_EVENT, message)

    async def _trigger_intercom(
        self,
        active: bool,
        *,
        source: str,
        call_event: VideoCallEvent | None = None,
        alarm_type: int | None = None,
    ) -> None:
        """Fire the intercom in-call binary sensor and HASS event."""
        ctx = self.hass.data.get(DOMAIN, {}).get("notification_ctx")
        if not ctx:
            return

        from homeassistant.const import STATE_OFF, STATE_ON

        alert = AlertInfo(
            channel_id=0,
            io_port_id=0,
            event_id="intercom",
            state=STATE_ON if active else STATE_OFF,
        )
        ctx.device = self
        ctx.update_alert_channel(alert)
        try:
            ctx.trigger_sensor(alert)
        except ValueError:
            _LOGGER.debug("No intercom binary sensor registered for %s", self.device_info.serial_no)

        message = {
            "channel_id": 0,
            "io_port_id": 0,
            "camera_name": "",
            "event_id": "intercom",
            "source": source,
            "active": active,
        }
        if call_event is not None:
            message.update(
                {
                    "cmd_type": call_event.cmd_type,
                    "building_number": call_event.building_number,
                    "unit_number": call_event.unit_number,
                    "room_number": call_event.room_number,
                    "dev_index": call_event.dev_index,
                }
            )
        if alarm_type is not None:
            message["alarm_type"] = alarm_type
        self.hass.bus.fire(HIKVISION_EVENT, message)

    async def _trigger_calling(
        self,
        active: bool,
        *,
        source: str,
        call_event: VideoCallEvent | None = None,
        alarm_type: int | None = None,
    ) -> None:
        """Fire the calling binary sensor and HASS event."""
        ctx = self.hass.data.get(DOMAIN, {}).get("notification_ctx")
        if not ctx:
            return

        from homeassistant.const import STATE_OFF, STATE_ON

        alert = AlertInfo(
            channel_id=0,
            io_port_id=0,
            event_id="calling",
            state=STATE_ON if active else STATE_OFF,
        )
        ctx.device = self
        ctx.update_alert_channel(alert)
        try:
            ctx.trigger_sensor(alert)
        except ValueError:
            _LOGGER.debug("No calling binary sensor registered for %s", self.device_info.serial_no)

        message = {
            "channel_id": 0,
            "io_port_id": 0,
            "camera_name": "",
            "event_id": "calling",
            "source": source,
            "active": active,
        }
        if call_event is not None:
            message.update(
                {
                    "cmd_type": call_event.cmd_type,
                    "building_number": call_event.building_number,
                    "unit_number": call_event.unit_number,
                    "room_number": call_event.room_number,
                    "dev_index": call_event.dev_index,
                }
            )
        if alarm_type is not None:
            message["alarm_type"] = alarm_type
        self.hass.bus.fire(HIKVISION_EVENT, message)

    async def intercom_answer(self) -> None:
        """Answer an incoming intercom call."""
        await self._apply_intercom_call_cmd(
            VideoCallCmdType.ANSWER_CALL,
            source="command",
        )
        try:
            await self._send_intercom_command(
                VideoCallCmdType.ANSWER_CALL,
                apply_state=False,
            )
        except Exception:
            await self._apply_intercom_call_cmd(
                VideoCallCmdType.CANCEL_CALL,
                source="command_rollback",
            )
            raise

    async def intercom_call(self) -> None:
        """Initiate an outgoing video intercom call to the client."""
        await self._send_intercom_command(VideoCallCmdType.CALLING)

    async def intercom_reject(self) -> None:
        """Reject an incoming intercom call."""
        await self._send_intercom_command(VideoCallCmdType.REJECT_CALL)

    async def intercom_hangup(self) -> None:
        """End the active intercom call and release the device session."""
        for cmd in INTERCOM_HANGUP_RELEASE_CMDS:
            await self._send_intercom_command(
                cmd,
                apply_state=(cmd == VideoCallCmdType.END_CALL),
            )
        await self._finalize_intercom_hangup()

    async def _finalize_intercom_hangup(self) -> None:
        """Ensure HA state is idle after a multi-command hangup release."""
        self._cancel_calling_timeout()
        self._intercom_call_state = IntercomCallState.IDLE
        await self.async_set_intercom_call_state(IntercomCallState.IDLE)
        await self._trigger_calling(False, source="hangup")
        await self._trigger_intercom(False, source="hangup")

    async def _send_intercom_command(
        self,
        cmd_type: VideoCallCmdType,
        *,
        apply_state: bool = True,
    ) -> None:
        if self.connection_type != CONF_CONNECTION_SDK:
            raise ValueError("Video intercom control requires SDK connection type")
        if self._video_intercom is None:
            raise ValueError("Video intercom RemoteConfig is not active")

        await self.hass.async_add_executor_job(
            self._video_intercom.send_call_command,
            cmd_type,
        )

        if apply_state:
            await self._apply_intercom_call_cmd(int(cmd_type), source="command")

    def _event_matches_capabilities_scope(
        self, event: EventInfo, camera_id: int | None
    ) -> bool:
        """Return whether an event belongs in device- or camera-scoped entity lists."""
        if event.id not in EVENTS:
            return False
        if camera_id is None:
            if self.device_info.is_nvr and event.id == "anpr":
                return False
            event_type = EVENTS[event.id].get("type")
            return event_type == EVENT_IO or event.id in DEVICE_LEVEL_EVENT_IDS
        if event.channel_id == int(camera_id):
            return True
        if event.id == "anpr":
            return False
        return (
            self.device_info.is_nvr
            and event.channel_id == 0
            and event.id in DEVICE_LEVEL_EVENT_IDS
        )

    def get_device_event_capabilities(
        self,
        camera_id: int | None = None,
    ) -> list[EventInfo]:
        """Get events info handled by integration (camera id:  NVR = None, camera > 0)."""
        events = []

        if camera_id is None and self.capabilities.support_video_intercom:
            serial_slug = slugify(self.device_info.serial_no.lower())
            doorbell = EventInfo(id="doorbell", channel_id=0, io_port_id=0, disabled=False)
            doorbell.unique_id = f"{serial_slug}_doorbell"
            events.append(doorbell)
            calling = EventInfo(id="calling", channel_id=0, io_port_id=0, disabled=False)
            calling.unique_id = f"{serial_slug}_calling"
            events.append(calling)
            intercom = EventInfo(id="intercom", channel_id=0, io_port_id=0, disabled=False)
            intercom.unique_id = f"{serial_slug}_intercom"
            events.append(intercom)

        integration_supported_events = [
            event
            for event in self.supported_events
            if self._event_matches_capabilities_scope(event, camera_id)
        ]

        for event in integration_supported_events:
            if not EVENTS.get(event.id):
                continue

            # Build unique_id
            device_id_param = f"_{camera_id}" if camera_id else ""
            io_port_id_param = f"_{event.io_port_id}" if event.io_port_id != 0 else ""
            serial = slugify(self.device_info.serial_no.lower())
            unique_id = f"{serial}{device_id_param}{io_port_id_param}_{event.id}"
            anpr_plate_unique_id = None
            anpr_image_unique_id = None
            if event.id == "anpr":
                anpr_plate_unique_id = (
                    f"{serial}{device_id_param}{io_port_id_param}_{ANPR_LICENSE_PLATE_SENSOR_SUFFIX}"
                )
                anpr_image_unique_id = (
                    f"{serial}{device_id_param}{io_port_id_param}_{ANPR_IMAGE_SUFFIX}"
                )

            events.append(
                replace(
                    event,
                    unique_id=unique_id,
                    anpr_plate_unique_id=anpr_plate_unique_id,
                    anpr_image_unique_id=anpr_image_unique_id,
                    disabled="center" not in event.notifications,
                )
            )
        return events

    def handle_exception(self, ex: Exception, details: str = ""):
        """Handle common exceptions."""

        error = "Unexpected exception"

        if isinstance(ex, ISAPIUnauthorizedError):
            if not self.auth_token_expired:
                # after device reboot, authorization token may have expired
                self.auth_token_expired = True
                self._auth_method = None
                self._session = get_async_client(self.hass, self.verify_ssl)
                _LOGGER.warning("Unauthorized access to %s, started checking if token expired", self.host)
                return
            self.auth_token_expired = False
            self.entry.async_start_reauth(self.hass)
            error = "Unauthorized access"
        elif isinstance(ex, ISAPIForbiddenError):
            error = "Forbidden access"
        elif isinstance(ex, (httpx.TimeoutException, httpx.ConnectTimeout)):
            error = "Timeout"
        elif isinstance(ex, (httpx.ConnectError, httpx.NetworkError)):
            error = "Connection error"

        _LOGGER.warning("%s | %s | %s | %s", error, self.host, details, ex)

    async def _dispatch_subscribed_event(self, raw_event: str | dict) -> None:
        """
        Local handling for subscribe events.
        """
        if isinstance(raw_event, str) and '\n<SubscribeEventResponse>' in raw_event:
            return

        if raw_event is None:
            await self.async_set_subscribe_connected(False, "stream error")
            return

        # Healthy event received → mark subscription as connected
        await self.async_set_subscribe_connected(True)

        if self.hass.data.get(DOMAIN) and self.hass.data[DOMAIN].get("notification_ctx"):
            ctx = self.hass.data[DOMAIN]["notification_ctx"]
            ctx.device = self
            await ctx.handle_subscribed_event(raw_event, device=self)

    async def async_set_subscribe_connected(self, connected: bool, reason: str | None = None):
        """Update the event/alarm channel connectivity status."""
        if self.subscribe_coordinator:
            await self.subscribe_coordinator.async_set_connected(connected, reason)
        if connected:
            self._cancel_subscribe_reconnect()
            self._subscribe_reconnect_attempts = 0
        elif reason != "stopped":
            self._schedule_subscribe_reconnect(reason)

    async def async_set_intercom_call_state(self, call_state: IntercomCallState) -> None:
        """Update the video intercom call state for UI controls."""
        if self.intercom_coordinator:
            await self.intercom_coordinator.async_set_call_state(call_state)

    async def async_set_intercom_connected(self, connected: bool, reason: str | None = None):
        """Update the video intercom RemoteConfig connectivity status."""
        if self.intercom_coordinator:
            await self.intercom_coordinator.async_set_connected(connected, reason)
        if connected:
            self._cancel_intercom_reconnect()
            self._intercom_reconnect_attempts = 0
        elif reason != "stopped":
            self._schedule_intercom_reconnect(reason)