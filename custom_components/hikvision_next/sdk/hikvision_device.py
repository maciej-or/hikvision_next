"ISAPI client for Home Assistant integration."
import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util import slugify

from .const import (
    ALARM_SERVER_PATH,
    CONF_ALARM_SERVER_HOST,
    CONF_CONNECTION_HTTP_NOTIFY,
    CONF_SET_ALARM_SERVER,
    DOMAIN,
    HIKVISION_EVENT,
    resolve_connection_type,
    EVENTS,
    EVENTS_COORDINATOR,
    RTSP_PORT_FORCED,
    SECONDARY_COORDINATOR, CONF_CONNECTION_SDK,
)

from .coordinator import EventsCoordinator, SecondaryCoordinator, SubscribeStatusCoordinator
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
    COMM_ALARM, COMM_ALARM_V40, NET_VCA_RULE_ALARM, NET_VCA_FACESNAP_RESULT, NET_VCA_FACESNAP_MATCH_ALARM, \
    NET_DVR_VEHICLE_CONTROL_LIST_DSALARM, NET_DVR_GATE_ALARMINFO, NET_DVR_ALARMINFO, NET_DVR_ALARMINFO_V40, \
    MAX_CHANNUM_V30, MAX_DISKNUM_V30, ALARMINFO_V30_ALARMTYPE_SEMAPHORE_ALARM, ALARMINFO_V30_ALARMTYPE_VIDEO_LOST, \
    ALARMINFO_V30_ALARMTYPE_TAMPERING_DETECTION, ALARMINFO_V30_ALARMTYPE_INTELLIGENT_SCENE_CHANGED
from .sdk.hcnetsdk import ALARMINFO_V30_ALARMTYPE_MOTION_DETECTION, BOOL, COMM_ALARM_V30, COMM_ALARM_VIDEO_INTERCOM, COMM_UPLOAD_VIDEO_INTERCOM_EVENT, DWORD, LONG, NET_DVR_ALARMER, NET_DVR_ALARMINFO_V30, NET_DVR_VIDEO_INTERCOM_ALARM, NET_DVR_VIDEO_INTERCOM_EVENT, NET_DVR_ALARM_ISAPI_INFO, NET_DVR_ACS_ALARM_INFO, COMM_ISAPI_ALARM, COMM_ALARM_ACS, MessageCallbackAlarmInfoUnion
from .sdk.hcnetsdk import VIDEO_INTERCOM_ALARM_ALARMTYPE_DISMISS_INCOMING_CALL, VIDEO_INTERCOM_ALARM_ALARMTYPE_DOORBELL_RINGING
from .sdk.utils import (
    SDKError,
    build_gate_alarm_anpr_event,
    build_vehicle_control_anpr_event,
    build_vehicle_control_list_dsalarm_event,
    call_ISAPI,
    map_vca_rule_event,
    read_sdk_alarm_image_buffer,
    sdk_ptz_control_other,
    structToDict,
)
from .sdk.vehicle_control_list import fetch_vehicle_control_list
from .sdk.video_intercom import VideoCallEvent, VideoCallCmdType, VideoIntercomRemoteConfig

_LOGGER = logging.getLogger(__name__)


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
        self.sdk_subscription: Any = None
        self.sdk_user: Any = None
        self.sdk_handle: LONG | None = None
        self.sdk_callback_func = None
        self._ptz_active: dict[int, dict[str, int]] = {}
        self._video_intercom: VideoIntercomRemoteConfig | None = None

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

            alarm_param = NET_DVR_SETUPALARM_PARAM_V50()
            alarm_param.dwSize = sizeof(NET_DVR_SETUPALARM_PARAM_V50)
            alarm_param.byLevel = 2
            alarm_param.byAlarmInfoType = 1
            alarm_param.byFaceAlarmDetection = 1
            alarm_param.byDeployType = 1
            # bit3: face snap, bit4: face contrast (IPC)
            alarm_param.bySupport = alarm_param.bySupport | 0x18
            # bit3: subscribe to COMM_ALARM_RULE (VCA behavior detection)
            alarm_param.byBrokenNetHttpV60 = alarm_param.byBrokenNetHttpV60 | 0x08
            # This flips bit 1 to 0, telling the doorbell NOT to send the backlog.
            alarm_param.bySupport = alarm_param.bySupport & ~0x02

            _LOGGER.debug("Arming the device via SDK")
            alarm_handle = self.sdk_subscription.NET_DVR_SetupAlarmChan_V50(
                self.sdk_user, alarm_param, None, 0)
            if alarm_handle < 0:
                errno = self.sdk_subscription.NET_DVR_GetLastError()
                _LOGGER.error(f"Error while listening to events, errno={errno}: {self.sdk_subscription.NET_DVR_GetErrorMsg(c_long(errno)).decode('utf-8')}")
                await self.async_set_subscribe_connected(False, f"{errno}: {self.sdk_subscription.NET_DVR_GetErrorMsg(c_long(errno)).decode('utf-8')}")
                # return
                # raise SDKError(self.sdk_subscription, f"Error while listening to events")
            else:
                await self.async_set_subscribe_connected(True)
                self.sdk_handle = alarm_handle

            if self.capabilities.support_video_intercom:
                await self._start_video_intercom_remote_config()

        # first data fetch
        for coordinator in self.coordinators.values():
            await coordinator.async_config_entry_first_refresh()

    def _handle_callback(self, command: int, alarm_device_pointer, alarm_info_pointer, buffer_length, user_pointer):
        _LOGGER.debug("Callback invoked from SDK")
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

        if command not in [
            COMM_ALARM_ACS,
            COMM_UPLOAD_FACESNAP_RESULT,
            COMM_ALARM_RULE,
            COMM_VEHICLE_CONTROL_ALARM,
            COMM_VEHICLE_CONTROL_LIST_DSALARM,
            COMM_ITS_GATE_ALARMINFO,
        ]:
            sdk_list = dir(hcnetsdk)
            sCmd = "0x%04x" % command
            for k in sdk_list:
                if k.startswith("COMM_") and getattr(hcnetsdk, k) == command:
                    sCmd = k

            try:
                class BytesEncoder(json.JSONEncoder):
                    def default(self, obj):
                        if isinstance(obj, (bytes, bytearray)):
                            return "hex:"+obj.hex()  # 推荐：十六进制
                            # return list(obj)                  # 转列表
                            # return obj.decode('utf-8', errors='replace')  # 尝试解码
                        return super().default(obj)

                _LOGGER.info(f"Receive unknow cmd {sCmd} %s", json.dumps(structToDict(alarm_info), cls=BytesEncoder, indent=4))
            except TypeError:
                _LOGGER.info(f"Receive unknow cmd {sCmd} %s", structToDict(alarm_info))

        # COMM_CROSSLINE_ALARM

        match alarm_info:
            case NET_DVR_ALARMINFO_V30():
                evt_type_map = {
                    ALARMINFO_V30_ALARMTYPE_MOTION_DETECTION: "MotionDetection",
                    ALARMINFO_V30_ALARMTYPE_VIDEO_LOST: "VideoLoss",
                    ALARMINFO_V30_ALARMTYPE_TAMPERING_DETECTION: "TamperDetection",
                    ALARMINFO_V30_ALARMTYPE_INTELLIGENT_SCENE_CHANGED: "SceneChangeDetection",
                    ALARMINFO_V30_ALARMTYPE_SEMAPHORE_ALARM: "IO"
                }
                channel_bitmap = list(alarm_info.byChannel)
                channel_id = channel_from_bitmap(channel_bitmap)
                raw_event = {
                    "eventType": evt_type_map.get(alarm_info.dwAlarmType),
                    "alarmTypeID": alarm_info.dwAlarmType,
                    "inputIOPortID": alarm_info.dwAlarmInputNumber,
                    "channelID": channel_id,
                    "videoInputChannelID": channel_id,
                    "AlarmOutputNumber": list(alarm_info.byAlarmOutputNumber),
                    "AlarmRelateChannel": list(alarm_info.byAlarmRelateChannel),
                    "channels": channel_bitmap,
                    "diskNumbers": list(alarm_info.byDiskNumber)
                }
            case NET_DVR_VIDEO_INTERCOM_ALARM():
                await self._handle_video_intercom_alarm(alarm_info)
            case NET_DVR_VIDEO_INTERCOM_EVENT():
                await self._handle_video_intercom_event(alarm_info)
            case NET_DVR_ALARM_ISAPI_INFO():
                pass
            case NET_DVR_VEHICLE_CONTROL_ALARM():
                raw_event = build_vehicle_control_anpr_event(alarm_info)
                if alarm_info.dwPicDataLen and alarm_info.byPicTransType == 1:
                    _LOGGER.debug(
                        "Vehicle control ANPR on channel %s uses URL image upload; "
                        "picture bytes not included in SDK callback",
                        alarm_info.dwChannel,
                    )
                elif alarm_info.dwPicDataLen:
                    image_bytes = read_sdk_alarm_image_buffer(
                        alarm_info.pPicData,
                        alarm_info.dwPicDataLen,
                        alarm_info.byPicTransType,
                    )
                    if image_bytes:
                        _LOGGER.debug(
                            "Vehicle control ANPR on channel %s includes %s byte JPEG",
                            alarm_info.dwChannel,
                            len(image_bytes),
                        )
            case NET_DVR_ACS_ALARM_INFO():
                acs_evt = alarm_info.struAcsEventInfo
                raw_event = {
                    "eventType": "AccessControllerEvent",
                    "AccessControllerEvent": {
                        "majorEventType": alarm_info.dwMajor,
                        "subEventType": alarm_info.dwMinor,
                        "employeeNoString": acs_evt.dwEmployeeNo,
                        "name": None,
                        "channelID": acs_evt.byReportChannel
                    }
                }
            case NET_DVR_ALARMINFO():
                raw_event = {
                    "eventType": "alarmInfo",
                    "alarmTypeID": alarm_info.dwAlarmType,
                    "diskNumber": alarm_info.dwDiskNumber,
                    "inputIOPortID": alarm_info.dwAlarmInputNumber,
                    "videoInputChannelID": alarm_info.dwChannel,
                    "outputIOPortID": alarm_info.dwAlarmOutputNumber,
                    "relateChannelID": alarm_info.dwAlarmRelateChannel
                }
            case NET_DVR_ALARMINFO_V40():
                pass
            case NET_VCA_RULE_ALARM():
                rule_info = alarm_info.struRuleInfo
                event_id = map_vca_rule_event(rule_info.wEventTypeEx, rule_info.dwEventType)
                if not event_id:
                    _LOGGER.warning(
                        "Unhandled VCA rule event wEventTypeEx=%s dwEventType=%s",
                        rule_info.wEventTypeEx,
                        rule_info.dwEventType,
                    )
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
                await self._handle_vehicle_control_list_dsalarm(alarm_info)
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
            await ctx.handle_subscribed_event(raw_event)

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
        elif command == COMM_ITS_GATE_ALARMINFO:
            return cast(callback_alarm_info_p, POINTER(NET_DVR_GATE_ALARMINFO)).contents
        else:
            _LOGGER.warning("Received unhandled command: %d", command)
            return callback_alarm_info_p

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

    async def _start_video_intercom_remote_config(self) -> None:
        """Subscribe to call signals via NET_DVR_StartRemoteConfig."""
        if self.connection_type != CONF_CONNECTION_SDK or self.sdk_user is None:
            return

        def on_call_event(event: VideoCallEvent) -> None:
            asyncio.run_coroutine_threadsafe(
                self._handle_video_call_event(event),
                self.hass.loop,
            )

        session = VideoIntercomRemoteConfig(
            self.sdk_subscription,
            self.sdk_user,
            on_call_event,
        )

        try:
            await self.hass.async_add_executor_job(session.start)
        except SDKError as ex:
            _LOGGER.error("Failed to start video intercom RemoteConfig: %s", ex)
            return

        self._video_intercom = session
        _LOGGER.info("Video intercom call signal listener active")

    async def _stop_video_intercom_remote_config(self) -> None:
        """Stop NET_DVR_StopRemoteConfig session."""
        session = self._video_intercom
        if session is None:
            return
        await self.hass.async_add_executor_job(session.stop)
        self._video_intercom = None

    async def _handle_video_call_event(self, event: VideoCallEvent) -> None:
        """Handle NET_DVR_VIDEO_CALL_PARAM events from RemoteConfig."""
        if event.doorbell_active is None:
            return
        await self._trigger_doorbell(event.doorbell_active, source="remote_config", call_event=event)

    async def _handle_vehicle_control_list_dsalarm(
        self, alarm_info: NET_DVR_VEHICLE_CONTROL_LIST_DSALARM
    ) -> None:
        """Handle COMM_VEHICLE_CONTROL_LIST_DSALARM (blocklist/allowlist sync notification)."""
        payload = build_vehicle_control_list_dsalarm_event(alarm_info)
        entries: list[dict] = []
        if self.sdk_user is not None and self.sdk_subscription is not None:
            try:
                entries = await self.hass.async_add_executor_job(
                    fetch_vehicle_control_list,
                    self.sdk_subscription,
                    self.sdk_user,
                    alarm_info.dwDataIndex,
                )
            except SDKError as ex:
                _LOGGER.warning(
                    "Failed to fetch vehicle control list on %s from data_index=%s: %s",
                    self.device_info.serial_no,
                    payload["data_index"],
                    ex,
                )
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception(
                    "Vehicle control list fetch failed on %s (data_index=%s)",
                    self.device_info.serial_no,
                    payload["data_index"],
                )
        payload["entries"] = entries
        payload["entry_count"] = len(entries)
        _LOGGER.info(
            "Vehicle control list sync on %s: data_index=%s operate_index=%s entries=%s",
            self.device_info.serial_no,
            payload["data_index"],
            payload["operate_index"] or "(empty)",
            len(entries),
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
        if alarm_type == VIDEO_INTERCOM_ALARM_ALARMTYPE_DOORBELL_RINGING:
            await self._trigger_doorbell(True, source="alarm", alarm_type=alarm_type)
        elif alarm_type == VIDEO_INTERCOM_ALARM_ALARMTYPE_DISMISS_INCOMING_CALL:
            await self._trigger_doorbell(False, source="alarm", alarm_type=alarm_type)

    async def _handle_video_intercom_event(self, alarm_info: NET_DVR_VIDEO_INTERCOM_EVENT) -> None:
        """Handle COMM_UPLOAD_VIDEO_INTERCOM_EVENT callback (logged for now)."""
        _LOGGER.debug(
            "Video intercom event type %s from device %s",
            alarm_info.byEventType,
            bytes(alarm_info.byDevNumber).split(b"\x00")[0],
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

    async def intercom_answer(self) -> None:
        """Answer an incoming intercom call."""
        await self._send_intercom_command(VideoCallCmdType.ANSWER_CALL)

    async def intercom_reject(self) -> None:
        """Reject an incoming intercom call."""
        await self._send_intercom_command(VideoCallCmdType.REJECT_CALL)

    async def intercom_hangup(self) -> None:
        """End the active intercom call."""
        await self._send_intercom_command(VideoCallCmdType.END_CALL)

    async def _send_intercom_command(self, cmd_type: VideoCallCmdType) -> None:
        if self.connection_type != CONF_CONNECTION_SDK:
            raise ValueError("Video intercom control requires SDK connection type")
        if self._video_intercom is None:
            raise ValueError("Video intercom RemoteConfig is not active")

        await self.hass.async_add_executor_job(
            self._video_intercom.send_call_command,
            cmd_type,
        )

    def get_device_event_capabilities(
        self,
        camera_id: int | None = None,
    ) -> list[EventInfo]:
        """Get events info handled by integration (camera id:  NVR = None, camera > 0)."""
        events = []

        if camera_id is None and self.capabilities.support_video_intercom:
            doorbell = EventInfo(id="doorbell", channel_id=0, io_port_id=0, disabled=False)
            unique_id = f"{slugify(self.device_info.serial_no.lower())}_doorbell"
            doorbell.unique_id = unique_id
            events.append(doorbell)

        if camera_id is None:  # NVR
            integration_supported_events = [
                s for s in self.supported_events if (s.id in EVENTS and EVENTS[s.id].get("type") == EVENT_IO)
            ]
        else:  # Camera
            integration_supported_events = [
                s for s in self.supported_events if (s.channel_id == int(camera_id) and s.id in EVENTS)
            ]

        for event in integration_supported_events:
            # Build unique_id
            device_id_param = f"_{camera_id}" if camera_id else ""
            io_port_id_param = f"_{event.io_port_id}" if event.io_port_id != 0 else ""
            unique_id = f"{slugify(self.device_info.serial_no.lower())}{device_id_param}{io_port_id_param}_{event.id}"

            if EVENTS.get(event.id):
                event.unique_id = unique_id
                event.disabled = "center" not in event.notifications  # Disable if not set Notify Surveillance Center
                events.append(event)
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
            await ctx.handle_subscribed_event(raw_event)

    async def async_set_subscribe_connected(self, connected: bool, reason: str | None = None):
        """Update the subscribe status via its coordinator (按需更新)."""
        if self.subscribe_coordinator:
            await self.subscribe_coordinator.async_set_connected(connected, reason)