"""Helpers for parsing ACS (access control) SDK alarm events."""

from __future__ import annotations

from ctypes import POINTER, cast

from .hcnetsdk import (
    NET_DVR_ACS_ALARM_INFO,
    NET_DVR_ACS_EVENT_INFO_EXTEND,
    NET_DVR_ACS_EVENT_INFO_EXTEND_V20,
)
from .utils import decode_sdk_c_string


def build_acs_access_controller_event(alarm_info: NET_DVR_ACS_ALARM_INFO) -> dict:
    """Build an ISAPI-compatible AccessControllerEvent dict from an SDK ACS alarm."""
    acs_evt = alarm_info.struAcsEventInfo
    employee_no = ""
    verify_mode: int | None = None

    if getattr(alarm_info, "byAcsEventInfoExtend", 0) == 1 and alarm_info.pAcsEventInfoExtend:
        extend = cast(alarm_info.pAcsEventInfoExtend, POINTER(NET_DVR_ACS_EVENT_INFO_EXTEND)).contents
        employee_no = decode_sdk_c_string(extend.byEmployeeNo)
        verify_mode = extend.byCurrentVerifyMode

    if not employee_no and acs_evt.dwEmployeeNo:
        employee_no = str(acs_evt.dwEmployeeNo)

    name = None
    if getattr(alarm_info, "byAcsEventInfoExtendV20", 0) == 1 and alarm_info.pAcsEventInfoExtendV20:
        extend_v20 = cast(
            alarm_info.pAcsEventInfoExtendV20,
            POINTER(NET_DVR_ACS_EVENT_INFO_EXTEND_V20),
        ).contents
        label = decode_sdk_c_string(extend_v20.byAttendanceLabel)
        if label:
            name = label

    card_no = decode_sdk_c_string(acs_evt.byCardNo)

    ace: dict = {
        "majorEventType": alarm_info.dwMajor,
        "subEventType": alarm_info.dwMinor,
        "employeeNoString": employee_no,
        "cardNo": card_no or None,
        "name": name,
        "channelID": acs_evt.byReportChannel or 0,
        "doorNo": acs_evt.dwDoorNo or None,
    }
    if verify_mode is not None:
        ace["currentVerifyMode"] = verify_mode

    return {
        "eventType": "AccessControllerEvent",
        "AccessControllerEvent": ace,
    }