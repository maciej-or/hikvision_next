"""Video intercom RemoteConfig session helpers."""

from __future__ import annotations

import logging
from ctypes import CDLL, POINTER, addressof, byref, c_void_p, cast, sizeof
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable

from .hcnetsdk import (
    DWORD,
    ENUM_VIDEO_INTERCOM_SEND_DATA,
    LONG,
    NET_DVR_CALLBACK_STATUS_SEND_WAIT,
    NET_DVR_VIDEO_CALL_COND,
    NET_DVR_VIDEO_CALL_PARAM,
    NET_DVR_VIDEO_CALL_SIGNAL_PROCESS,
    NET_SDK_CALLBACK_STATUS_DEV_TYPE_MISMATCH,
    NET_SDK_CALLBACK_STATUS_EXCEPTION,
    NET_SDK_CALLBACK_STATUS_FAILED,
    NET_SDK_CALLBACK_STATUS_LANGUAGE_MISMATCH,
    NET_SDK_CALLBACK_STATUS_PROCESSING,
    NET_SDK_CALLBACK_STATUS_SUCCESS,
    NET_SDK_CALLBACK_TYPE_DATA,
    NET_SDK_CALLBACK_TYPE_PROGRESS,
    NET_SDK_CALLBACK_TYPE_STATUS,
    NET_SDK_CONFIG_STATUS_EXCEPTION,
    NET_SDK_CONFIG_STATUS_FAILED,
    NET_SDK_CONFIG_STATUS_FINISH,
    NET_SDK_CONFIG_STATUS_NEEDWAIT,
    VideoCallCmdType,
    fRemoteConfigCallback,
)
from .utils import SDKError

_LOGGER = logging.getLogger(__name__)

_CALLBACK_STATUS_NAMES = {
    NET_SDK_CALLBACK_STATUS_SUCCESS: "NET_SDK_CALLBACK_STATUS_SUCCESS",
    NET_SDK_CALLBACK_STATUS_PROCESSING: "NET_SDK_CALLBACK_STATUS_PROCESSING",
    NET_SDK_CALLBACK_STATUS_FAILED: "NET_SDK_CALLBACK_STATUS_FAILED",
    NET_SDK_CALLBACK_STATUS_EXCEPTION: "NET_SDK_CALLBACK_STATUS_EXCEPTION",
    NET_SDK_CALLBACK_STATUS_LANGUAGE_MISMATCH: "NET_SDK_CALLBACK_STATUS_LANGUAGE_MISMATCH",
    NET_SDK_CALLBACK_STATUS_DEV_TYPE_MISMATCH: "NET_SDK_CALLBACK_STATUS_DEV_TYPE_MISMATCH",
    NET_DVR_CALLBACK_STATUS_SEND_WAIT: "NET_DVR_CALLBACK_STATUS_SEND_WAIT",
}


class IntercomCallState(IntEnum):
    IDLE = 0
    RINGING = 1
    IN_CALL = 2


DOORBELL_PULSE_SECONDS = 1.0
# Fallback when the device does not send CANCEL/END/TIMEOUT after ringing.
DEFAULT_CALLING_RING_SECONDS = 30.0
INTERCOM_RECONNECT_BASE_DELAY = 5.0
INTERCOM_RECONNECT_MAX_DELAY = 120.0
SUBSCRIBE_ALARM_RECONNECT_BASE_DELAY = 5.0
SUBSCRIBE_ALARM_RECONNECT_MAX_DELAY = 120.0


DOORBELL_ON_CMD_TYPES = frozenset(
    {
        VideoCallCmdType.CALLING,
        VideoCallCmdType.INDOOR_STATION_RINGING,
    }
)

DOORBELL_OFF_CMD_TYPES = frozenset(
    {
        VideoCallCmdType.CANCEL_CALL,
        VideoCallCmdType.REJECT_CALL,
        VideoCallCmdType.DOOR_STATION_TIMEOUT,
        VideoCallCmdType.END_CALL,
    }
)

RINGING_CMD_TYPES = frozenset(
    {
        VideoCallCmdType.CALLING,
        VideoCallCmdType.INDOOR_STATION_RINGING,
    }
)

IN_CALL_ON_CMD_TYPES = frozenset(
    {
        VideoCallCmdType.DEVICE_IN_CALL,
        VideoCallCmdType.CLIENT_IN_CALL,
    }
)

IN_CALL_OFF_CMD_TYPES = DOORBELL_OFF_CMD_TYPES


@dataclass
class VideoCallEvent:
    """Parsed video intercom call signal."""

    cmd_type: int
    period: int
    building_number: int
    unit_number: int
    floor_number: int
    room_number: int
    dev_index: int
    unit_type: int
    doorbell_active: bool | None = None
    in_call_active: bool | None = None


def is_video_intercom_device(*, device_type: str = "", model: str = "") -> bool:
    """Return True when the device looks like a Hikvision video intercom."""
    device_type_l = (device_type or "").lower()
    model_u = (model or "").upper()
    return (
        "intercom" in device_type_l
        or "door station" in device_type_l
        or "doorstation" in device_type_l
        or model_u.startswith("DS-K")
    )


def map_video_call_cmd_to_doorbell(cmd_type: int) -> bool | None:
    """Map NET_DVR_VIDEO_CALL_PARAM.dwCmdType to doorbell on/off, or None if unrelated."""
    try:
        cmd = VideoCallCmdType(cmd_type)
    except ValueError:
        return None
    if cmd in DOORBELL_ON_CMD_TYPES:
        return True
    if cmd in DOORBELL_OFF_CMD_TYPES:
        return False
    return None


def map_video_call_cmd_to_in_call(cmd_type: int) -> bool | None:
    """Map NET_DVR_VIDEO_CALL_PARAM.dwCmdType to active call on/off, or None if unrelated."""
    try:
        cmd = VideoCallCmdType(cmd_type)
    except ValueError:
        return None
    if cmd in IN_CALL_ON_CMD_TYPES:
        return True
    if cmd in IN_CALL_OFF_CMD_TYPES or cmd in RINGING_CMD_TYPES:
        return False
    return None


def next_intercom_call_state(
    cmd_type: int,
    current: IntercomCallState = IntercomCallState.IDLE,
) -> IntercomCallState | None:
    """Advance tracked call state for a video intercom cmd, or None if unrelated."""
    try:
        cmd = VideoCallCmdType(cmd_type)
    except ValueError:
        return None
    if cmd in IN_CALL_OFF_CMD_TYPES:
        return IntercomCallState.IDLE
    if cmd in RINGING_CMD_TYPES:
        if current == IntercomCallState.IN_CALL:
            return None
        return IntercomCallState.RINGING
    if cmd in IN_CALL_ON_CMD_TYPES or cmd == VideoCallCmdType.ANSWER_CALL:
        return IntercomCallState.IN_CALL
    return None


def intercom_sensors_for_state(state: IntercomCallState) -> tuple[bool, bool]:
    """Return (calling_active, in_call_active) for a call state."""
    if state == IntercomCallState.IDLE:
        return False, False
    if state == IntercomCallState.RINGING:
        return True, False
    return False, True


def parse_video_call_param(param: NET_DVR_VIDEO_CALL_PARAM) -> VideoCallEvent:
    """Convert an SDK structure into a plain event object."""
    cmd_type = int(param.dwCmdType)
    return VideoCallEvent(
        cmd_type=cmd_type,
        period=int(param.wPeriod),
        building_number=int(param.wBuildingNumber),
        unit_number=int(param.wUnitNumber),
        floor_number=int(param.wFloorNumber),
        room_number=int(param.wRoomNumber),
        dev_index=int(param.wDevIndex),
        unit_type=int(param.byUnitType),
        doorbell_active=map_video_call_cmd_to_doorbell(cmd_type),
        in_call_active=map_video_call_cmd_to_in_call(cmd_type),
    )


def build_video_call_param(
    cmd_type: VideoCallCmdType,
    *,
    period: int = 0,
    building_number: int = 0,
    unit_number: int = 0,
    floor_number: int = 0,
    room_number: int = 0,
    dev_index: int = 0,
    unit_type: int = 0,
) -> NET_DVR_VIDEO_CALL_PARAM:
    """Build NET_DVR_VIDEO_CALL_PARAM for SendRemoteConfig."""
    param = NET_DVR_VIDEO_CALL_PARAM()
    param.dwSize = sizeof(NET_DVR_VIDEO_CALL_PARAM)
    param.dwCmdType = int(cmd_type)
    param.wPeriod = period
    param.wBuildingNumber = building_number
    param.wUnitNumber = unit_number
    param.wFloorNumber = floor_number
    param.wRoomNumber = room_number
    param.wDevIndex = dev_index
    param.byUnitType = unit_type
    return param


def copy_video_call_param(param: NET_DVR_VIDEO_CALL_PARAM) -> NET_DVR_VIDEO_CALL_PARAM:
    """Copy an SDK call param structure."""
    return build_video_call_param(
        VideoCallCmdType(param.dwCmdType),
        period=param.wPeriod,
        building_number=param.wBuildingNumber,
        unit_number=param.wUnitNumber,
        floor_number=param.wFloorNumber,
        room_number=param.wRoomNumber,
        dev_index=param.wDevIndex,
        unit_type=param.byUnitType,
    )


def _callback_status_name(status: int) -> str:
    return _CALLBACK_STATUS_NAMES.get(status, f"unknown({status})")


def _buffer_address(lp_buffer, offset: int = 0) -> int:
    """Resolve a callback buffer pointer to a memory address."""
    if isinstance(lp_buffer, int):
        return lp_buffer + offset
    return addressof(lp_buffer) + offset


def _read_buffer_dword(lp_buffer, offset: int = 0) -> int | None:
    """Read a DWORD from an SDK RemoteConfig callback buffer."""
    if not lp_buffer:
        return None
    return cast(_buffer_address(lp_buffer, offset), POINTER(DWORD)).contents.value


class VideoIntercomRemoteConfig:
    """Manage NET_DVR_StartRemoteConfig for video call signal processing."""

    def __init__(
        self,
        sdk: CDLL,
        user_id: int,
        on_event: Callable[[VideoCallEvent], None],
        on_status_change: Callable[[bool, str | None], None] | None = None,
    ) -> None:
        self._sdk = sdk
        self._user_id = user_id
        self._on_event = on_event
        self._on_status_change = on_status_change
        self._handle: int | None = None
        self._callback: fRemoteConfigCallback | None = None
        self._last_call: NET_DVR_VIDEO_CALL_PARAM | None = None

    @property
    def handle(self) -> int | None:
        return self._handle

    @property
    def last_call(self) -> NET_DVR_VIDEO_CALL_PARAM | None:
        return self._last_call

    def start(self) -> None:
        """Start receiving video intercom call signals."""
        if self._handle is not None:
            return

        cond = NET_DVR_VIDEO_CALL_COND()
        cond.dwSize = sizeof(NET_DVR_VIDEO_CALL_COND)

        @fRemoteConfigCallback
        def callback(dw_type: int, lp_buffer, dw_buf_len: int, _user_data) -> None:
            self._handle_callback(dw_type, lp_buffer, dw_buf_len)

        self._callback = callback
        handle = self._sdk.NET_DVR_StartRemoteConfig(
            self._user_id,
            NET_DVR_VIDEO_CALL_SIGNAL_PROCESS,
            byref(cond),
            sizeof(cond),
            self._callback,
            None,
        )
        if handle < 0:
            raise SDKError(self._sdk, "NET_DVR_StartRemoteConfig video call signal failed")
        self._handle = handle
        _LOGGER.info("Video intercom RemoteConfig started (handle=%s)", handle)

    def stop(self) -> None:
        """Stop the remote config session."""
        if self._handle is None:
            return
        result = self._sdk.NET_DVR_StopRemoteConfig(self._handle)
        if not result:
            _LOGGER.warning(
                "NET_DVR_StopRemoteConfig failed: %s",
                self._sdk.NET_DVR_GetLastError(),
            )
        _LOGGER.info("Video intercom RemoteConfig stopped (handle=%s)", self._handle)
        self._handle = None
        self._callback = None
        self._last_call = None

    def send_call_command(
        self,
        cmd_type: VideoCallCmdType,
        *,
        call_target: NET_DVR_VIDEO_CALL_PARAM | None = None,
    ) -> None:
        """Send an intercom control command via NET_DVR_SendRemoteConfig."""
        if self._handle is None:
            raise SDKError(self._sdk, "Video intercom RemoteConfig is not active")

        if call_target is not None:
            param = copy_video_call_param(call_target)
            param.dwCmdType = int(cmd_type)
        elif self._last_call is not None and cmd_type in (
            VideoCallCmdType.ANSWER_CALL,
            VideoCallCmdType.REJECT_CALL,
            VideoCallCmdType.END_CALL,
        ):
            param = copy_video_call_param(self._last_call)
            param.dwCmdType = int(cmd_type)
        else:
            param = build_video_call_param(cmd_type)

        result = self._sdk.NET_DVR_SendRemoteConfig(
            self._handle,
            ENUM_VIDEO_INTERCOM_SEND_DATA,
            cast(byref(param), c_void_p),
            sizeof(param),
        )
        if not result:
            raise SDKError(
                self._sdk,
                f"NET_DVR_SendRemoteConfig failed for cmd {cmd_type.name}",
            )
        _LOGGER.info("Video intercom command sent: %s", cmd_type.name)

    def _notify_status_change(self, connected: bool, reason: str | None = None) -> None:
        if self._on_status_change is not None:
            self._on_status_change(connected, reason)

    def _handle_callback_data(self, lp_buffer, dw_buf_len: int) -> None:
        """Handle NET_SDK_CALLBACK_TYPE_DATA with NET_DVR_VIDEO_CALL_PARAM payload."""
        if not lp_buffer or dw_buf_len < sizeof(NET_DVR_VIDEO_CALL_PARAM):
            _LOGGER.debug(
                "Video intercom data callback ignored (buffer_len=%s)",
                dw_buf_len,
            )
            return

        param = cast(lp_buffer, POINTER(NET_DVR_VIDEO_CALL_PARAM)).contents
        self._last_call = copy_video_call_param(param)
        event = parse_video_call_param(param)
        _LOGGER.debug("Video intercom call event: %s", event)
        try:
            self._on_event(event)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Video intercom event handler failed")

    def _handle_callback_status(self, lp_buffer, dw_buf_len: int) -> None:
        """Handle NET_SDK_CALLBACK_TYPE_STATUS (NET_SDK_CALLBACK_STATUS_NORMAL)."""
        if not lp_buffer or dw_buf_len < sizeof(DWORD):
            _LOGGER.debug("Video intercom status callback without buffer")
            return

        status = _read_buffer_dword(lp_buffer)
        if status is None:
            return

        status_name = _callback_status_name(status)

        if status == NET_SDK_CALLBACK_STATUS_SUCCESS:
            _LOGGER.debug("Video intercom RemoteConfig %s", status_name)
            self._notify_status_change(True)
            return

        if status == NET_SDK_CALLBACK_STATUS_PROCESSING:
            _LOGGER.debug("Video intercom RemoteConfig %s", status_name)
            return

        if status == NET_SDK_CALLBACK_STATUS_FAILED:
            err_code = _read_buffer_dword(lp_buffer, 4) if dw_buf_len >= 8 else None
            reason = status_name
            if err_code is not None:
                reason = f"{status_name} (error_code={err_code})"
            _LOGGER.warning("Video intercom RemoteConfig %s", reason)
            self._notify_status_change(False, reason)
            return

        if status in (
            NET_SDK_CALLBACK_STATUS_EXCEPTION,
            NET_SDK_CALLBACK_STATUS_LANGUAGE_MISMATCH,
            NET_SDK_CALLBACK_STATUS_DEV_TYPE_MISMATCH,
            NET_DVR_CALLBACK_STATUS_SEND_WAIT,
        ):
            _LOGGER.warning("Video intercom RemoteConfig %s", status_name)
            if status != NET_DVR_CALLBACK_STATUS_SEND_WAIT:
                self._notify_status_change(False, status_name)
            return

        _LOGGER.debug(
            "Video intercom RemoteConfig unhandled callback status %s (buffer_len=%s)",
            status_name,
            dw_buf_len,
        )

    def _handle_callback_progress(self, lp_buffer, dw_buf_len: int) -> None:
        """Handle NET_SDK_CALLBACK_TYPE_PROGRESS; buffer is a DWORD progress value."""
        progress = _read_buffer_dword(lp_buffer)
        if progress is None:
            _LOGGER.debug("Video intercom progress callback without buffer")
            return
        _LOGGER.debug("Video intercom RemoteConfig progress %s%%", progress)

    def _handle_legacy_config_status(self, dw_type: int, dw_buf_len: int) -> None:
        """Fallback for older NET_SDK_CONFIG_STATUS_* callbacks."""
        if dw_type in (NET_SDK_CONFIG_STATUS_NEEDWAIT, NET_SDK_CONFIG_STATUS_FINISH):
            _LOGGER.debug("Video intercom legacy RemoteConfig status %s", dw_type)
            return
        if dw_type in (NET_SDK_CONFIG_STATUS_FAILED, NET_SDK_CONFIG_STATUS_EXCEPTION):
            _LOGGER.warning("Video intercom legacy RemoteConfig status %s", dw_type)
            self._notify_status_change(False, f"RemoteConfig status {dw_type}")
            return
        _LOGGER.debug(
            "Video intercom RemoteConfig unhandled legacy status %s (len=%s)",
            dw_type,
            dw_buf_len,
        )

    def _handle_callback(self, dw_type: int, lp_buffer, dw_buf_len: int) -> None:
        if dw_type == NET_SDK_CALLBACK_TYPE_DATA:
            self._handle_callback_data(lp_buffer, dw_buf_len)
            return
        if dw_type == NET_SDK_CALLBACK_TYPE_STATUS:
            self._handle_callback_status(lp_buffer, dw_buf_len)
            return
        if dw_type == NET_SDK_CALLBACK_TYPE_PROGRESS:
            self._handle_callback_progress(lp_buffer, dw_buf_len)
            return
        self._handle_legacy_config_status(dw_type, dw_buf_len)