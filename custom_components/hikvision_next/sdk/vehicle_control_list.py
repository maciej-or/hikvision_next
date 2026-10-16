"""Fetch vehicle blocklist/allowlist entries via SDK or ISAPI."""

from __future__ import annotations

import logging
import threading
from ctypes import CDLL, POINTER, byref, cast, sizeof
from typing import TYPE_CHECKING, Any

from ..isapi.const import POST
from .hcnetsdk import (
    DWORD,
    LONG,
    NET_DVR_GET_ALL_VEHICLE_CONTROL_LIST,
    NET_DVR_VEHICLE_CONTROL_COND,
    NET_DVR_VEHICLE_CONTROL_LIST_INFO,
    NET_SDK_CONFIG_STATUS_EXCEPTION,
    NET_SDK_CONFIG_STATUS_FAILED,
    NET_SDK_CONFIG_STATUS_FINISH,
    NET_SDK_CONFIG_STATUS_NEEDWAIT,
    NET_SDK_CONFIG_STATUS_SUCCESS,
    fRemoteConfigCallback,
)
from .utils import SDKError, decode_sdk_c_string, decode_sdk_license_plate

if TYPE_CHECKING:
    from ..hikvision_device import HikvisionDevice

_LOGGER = logging.getLogger(__name__)

VEHICLE_CONTROL_CHANNEL_ALL = 0xFFFFFFFF
VEHICLE_CONTROL_LIST_TYPE_ALL = 0xFF
SDK_VEHICLE_LIST_UNSUPPORTED_ERRORS = frozenset({9, 17, 23})
ISAPI_LP_LIST_PAGE_SIZE = 50

LIST_TYPE_NAMES: dict[int, str] = {
    0: "allow",
    1: "block",
}

ISAPI_LIST_TYPE_MAP: dict[str, tuple[int, str]] = {
    "whiteList": (0, "allow"),
    "blackList": (1, "block"),
    "allowList": (0, "allow"),
    "blockList": (1, "block"),
}


def sdk_error_code(exc: SDKError) -> int | None:
    """Extract the SDK error code from an SDKError."""
    if len(exc.args) > 1 and isinstance(exc.args[1], int):
        return exc.args[1]
    return None


def is_sdk_vehicle_list_unsupported(exc: SDKError) -> bool:
    """Return True when the device likely does not support SDK list fetch."""
    code = sdk_error_code(exc)
    return code in SDK_VEHICLE_LIST_UNSUPPORTED_ERRORS


def build_vehicle_control_cond(
    data_index: int,
    *,
    channel: int = VEHICLE_CONTROL_CHANNEL_ALL,
    list_type: int = VEHICLE_CONTROL_LIST_TYPE_ALL,
) -> NET_DVR_VEHICLE_CONTROL_COND:
    """Build query condition for incremental vehicle control list fetch."""
    cond = NET_DVR_VEHICLE_CONTROL_COND()
    cond.dwChannel = channel
    cond.dwOperateType = 0
    cond.byListType = list_type
    cond.dwDataIndex = data_index
    return cond


def build_lp_list_audit_search_xml(
    search_result_position: int = 0,
    max_results: int = ISAPI_LP_LIST_PAGE_SIZE,
    search_id: str = "hikvision_next",
) -> str:
    """Build ISAPI search body for Traffic/channels/{n}/searchLPListAudit."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<LPListAuditSearchDescription>
<searchID>{search_id}</searchID>
<maxResults>{max_results}</maxResults>
<searchResultPosition>{search_result_position}</searchResultPosition>
</LPListAuditSearchDescription>"""


def format_sdk_time_v30(time_info) -> str | None:
    """Format NET_DVR_TIME_V30 as an ISO-like local timestamp string."""
    if time_info.wYear == 0:
        return None
    return (
        f"{time_info.wYear:04d}-"
        f"{time_info.byMonth:02d}-"
        f"{time_info.byDay:02d} "
        f"{time_info.byHour:02d}:"
        f"{time_info.byMinute:02d}:"
        f"{time_info.bySecond:02d}"
    )


def parse_vehicle_control_list_entry(info: NET_DVR_VEHICLE_CONTROL_LIST_INFO) -> dict[str, Any]:
    """Map NET_DVR_VEHICLE_CONTROL_LIST_INFO to a plain dict."""
    list_type = info.byListType
    return {
        "channel": info.dwChannel,
        "data_index": info.dwDataIndex,
        "license_plate": decode_sdk_license_plate(info.sLicense),
        "list_type": list_type,
        "list_type_name": LIST_TYPE_NAMES.get(list_type),
        "plate_type": info.byPlateType,
        "plate_color": info.byPlateColor,
        "card_no": decode_sdk_c_string(info.sCardNo),
        "operate_index": decode_sdk_c_string(info.sOperateIndex),
        "start_time": format_sdk_time_v30(info.struStartTime),
        "stop_time": format_sdk_time_v30(info.struStopTime),
        "source": "sdk",
    }


def _normalize_isapi_node(node: Any) -> list[dict[str, Any]]:
    if not node:
        return []
    if isinstance(node, list):
        return [item for item in node if isinstance(item, dict)]
    if isinstance(node, dict):
        return [node]
    return []


def parse_lp_list_audit_search_result(result: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int | None]:
    """Parse ISAPI searchLPListAudit response into integration entry dicts."""
    root = result.get("LPListAuditSearchResult", result)
    info_list = root.get("LicensePlateInfoList", {})
    if not isinstance(info_list, dict):
        return [], 0, None

    num_matches = int(info_list.get("numOfMatches", 0) or 0)
    total_matches = info_list.get("totalMatches")
    total = int(total_matches) if total_matches is not None else None

    entries: list[dict[str, Any]] = []
    for item in _normalize_isapi_node(info_list.get("LicensePlateInfo")):
        list_type_raw = item.get("type", "")
        list_type, list_type_name = ISAPI_LIST_TYPE_MAP.get(
            list_type_raw,
            (-1, list_type_raw or None),
        )
        entries.append(
            {
                "channel": 1,
                "data_index": None,
                "license_plate": item.get("LicensePlate") or item.get("id") or "",
                "list_type": list_type,
                "list_type_name": list_type_name,
                "plate_type": item.get("plateCategory"),
                "plate_color": item.get("plateColor"),
                "card_no": "",
                "operate_index": item.get("id") or "",
                "start_time": item.get("effectiveTime"),
                "stop_time": item.get("expiredTime") or item.get("effectiveStopTime"),
                "create_time": item.get("createTime"),
                "source": "isapi",
            }
        )
    return entries, num_matches, total


async def fetch_vehicle_control_list_isapi(
    device: HikvisionDevice,
    *,
    channel: int = 1,
    page_size: int = ISAPI_LP_LIST_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """Fetch allow/block list entries via ISAPI searchLPListAudit."""
    entries: list[dict[str, Any]] = []
    position = 0
    total_matches: int | None = None
    path = f"Traffic/channels/{channel}/searchLPListAudit"

    while total_matches is None or position < total_matches:
        xml = build_lp_list_audit_search_xml(position, page_size)
        result = await device.request(POST, path, data=xml, quiet=True)
        if not result:
            break

        page_entries, num_matches, total = parse_lp_list_audit_search_result(result)
        entries.extend(page_entries)
        if total is not None:
            total_matches = total
        if num_matches <= 0:
            break
        position += num_matches
        if num_matches < page_size:
            break

    return entries


def fetch_vehicle_control_list(
    sdk: CDLL,
    user_id: int,
    data_index: int,
    *,
    channel: int = VEHICLE_CONTROL_CHANNEL_ALL,
    list_type: int = VEHICLE_CONTROL_LIST_TYPE_ALL,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """Fetch vehicle control list entries updated since data_index.

    Uses NET_DVR_StartRemoteConfig with command 3124. The device streams one
    NET_DVR_VEHICLE_CONTROL_LIST_INFO per SUCCESS callback until FINISH.
    """
    cond = build_vehicle_control_cond(
        data_index,
        channel=channel,
        list_type=list_type,
    )
    entries: list[dict[str, Any]] = []
    done = threading.Event()
    error_message: list[str | None] = [None]
    callback_holder: list[fRemoteConfigCallback | None] = [None]

    @fRemoteConfigCallback
    def callback(dw_type: int, lp_buffer, dw_buf_len: int, _user_data) -> None:
        if dw_type == NET_SDK_CONFIG_STATUS_NEEDWAIT:
            return
        if dw_type == NET_SDK_CONFIG_STATUS_FINISH:
            done.set()
            return
        if dw_type in (NET_SDK_CONFIG_STATUS_FAILED, NET_SDK_CONFIG_STATUS_EXCEPTION):
            error_message[0] = f"vehicle control list remote config status {dw_type}"
            done.set()
            return
        if dw_type != NET_SDK_CONFIG_STATUS_SUCCESS:
            _LOGGER.debug(
                "Vehicle control list unhandled remote config status %s (len=%s)",
                dw_type,
                dw_buf_len,
            )
            return
        if not lp_buffer or dw_buf_len < sizeof(NET_DVR_VEHICLE_CONTROL_LIST_INFO):
            return

        info = cast(lp_buffer, POINTER(NET_DVR_VEHICLE_CONTROL_LIST_INFO)).contents
        entries.append(parse_vehicle_control_list_entry(info))

    callback_holder[0] = callback
    handle = sdk.NET_DVR_StartRemoteConfig(
        user_id,
        NET_DVR_GET_ALL_VEHICLE_CONTROL_LIST,
        byref(cond),
        sizeof(cond),
        callback,
        None,
    )
    if handle < 0:
        raise SDKError(sdk, "NET_DVR_StartRemoteConfig vehicle control list failed")

    try:
        if not done.wait(timeout):
            _LOGGER.warning(
                "Vehicle control list fetch timed out after %ss (data_index=%s, entries=%s)",
                timeout,
                data_index,
                len(entries),
            )
        if error_message[0]:
            raise SDKError(sdk, error_message[0])
        return entries
    finally:
        sdk.NET_DVR_StopRemoteConfig(handle)
        callback_holder[0] = None