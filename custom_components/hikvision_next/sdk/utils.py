import logging
import pprint
from ctypes import CDLL, POINTER, c_char, c_char_p, c_int, c_long, c_void_p, cast, cdll, sizeof, string_at
from ctypes.wintypes import LPVOID
from enum import IntEnum
import os
import platform
from typing import Optional, TypedDict
from .hcnetsdk import (
    ALARMINFO_V30_ALARMTYPE_ILLEGAL_ACCESS,
    ALARMINFO_V30_ALARMTYPE_INTELLIGENT_SCENE_CHANGED,
    ALARMINFO_V30_ALARMTYPE_MOTION_DETECTION,
    ALARMINFO_V30_ALARMTYPE_SEMAPHORE_ALARM,
    ALARMINFO_V30_ALARMTYPE_TAMPERING_DETECTION,
    ALARMINFO_V30_ALARMTYPE_VIDEO_LOST,
    BOOL,
    DWORD,
    LONG,
    NET_DVR_DEVICEINFO_V30,
    NET_DVR_SETUPALARM_PARAM_V50,
    NET_DVR_XML_CONFIG_INPUT,
    NET_DVR_XML_CONFIG_OUTPUT,
    WORD,
    fMessageCallBack,
    fRemoteConfigCallback,
)

from ctypes import (POINTER, c_void_p, cast, sizeof, c_long, c_char, c_byte, memmove, c_char_p,
                    addressof, Structure, Array, Union)

logger = logging.getLogger(__name__)

class SDKLogLevel(IntEnum):
    '''
    Define the level of verbosity of the SDK.
    '''
    NONE = 0
    ERROR = 1
    INFO = 2
    DEBUG = 3


class SDKConfig(TypedDict):
    '''
    Configuration for the SDK

    Attributes:
        log_level: Level of verbosity. By default it is NONE.
        log_dir: Directory where the SDK will write the log files

    '''
    log_level: SDKLogLevel
    log_dir: str


def loadSDK() -> CDLL:
    '''
    Load the Hikvision SDK library with ctypes wrapper and return it.

    setupSDK() must be called before the library can be used.

    Returns:
       CDLL: The loaded library
    '''
    logger.info(f"Using OS: {platform.uname()[0]} with architecture: {platform.uname()[4]}")

    if platform.uname()[0] == "Windows":
        hcnetsdk_path = ".\\lib-windows64\\HCNetSDK.dll"
    elif platform.uname()[0] == "Linux":
        base_path = os.path.dirname(__file__)
        if platform.uname()[4] == "x86_64":
            hcnetsdk_path = os.path.join(base_path, "libs/lib-amd64", "libhcnetsdk.so")
        elif platform.uname()[4] == "aarch64":
            hcnetsdk_path = os.path.join(base_path, "libs/lib-aarch64", "libhcnetsdk.so")
        else:
            raise RuntimeError("No supported Linux library found!")
    else:
        raise RuntimeError("Unsupported operating system")
    logger.debug(f"Loading library from {hcnetsdk_path}")
    lib = cdll.LoadLibrary(hcnetsdk_path)
    setupFunctionTypes(lib)
    return lib


def setupFunctionTypes(lib: CDLL):
    """Define the argument types so that ctypes can help in avoiding error when calling the C functions."""

    # Arguments
    lib.NET_DVR_Login_V30.argtypes = [c_char_p, WORD, c_char_p, c_char_p, POINTER(NET_DVR_DEVICEINFO_V30)]
    lib.NET_DVR_Logout_V30.argtypes = [c_int]
    lib.NET_DVR_GetErrorMsg.argtypes = [POINTER(c_long)]
    lib.NET_DVR_SetDVRMessageCallBack_V50.argtypes = [c_int, fMessageCallBack, c_void_p]
    lib.NET_DVR_SetupAlarmChan_V50.argtypes = [LONG, NET_DVR_SETUPALARM_PARAM_V50, c_char_p, DWORD]
    lib.NET_DVR_RemoteControl.argtypes = [LONG, DWORD, c_void_p, DWORD]
    lib.NET_DVR_RemoteControl.restype = BOOL
    lib.NET_DVR_STDXMLConfig.argtypes = [LONG, POINTER(NET_DVR_XML_CONFIG_INPUT), POINTER(NET_DVR_XML_CONFIG_OUTPUT)]
    lib.NET_DVR_GetDeviceAbility.argtypes = [LONG, DWORD, c_char_p, DWORD, c_char_p, DWORD]
    lib.NET_DVR_PTZControl_Other.argtypes = [LONG, LONG, DWORD, DWORD]
    lib.NET_DVR_PTZControlWithSpeed_Other.argtypes = [LONG, LONG, DWORD, DWORD, DWORD]
    lib.NET_DVR_StartRemoteConfig.argtypes = [
        LONG,
        DWORD,
        c_void_p,
        DWORD,
        fRemoteConfigCallback,
        c_void_p,
    ]
    lib.NET_DVR_StopRemoteConfig.argtypes = [LONG]
    lib.NET_DVR_SendRemoteConfig.argtypes = [LONG, DWORD, c_void_p, DWORD]

    # Return types
    lib.NET_DVR_GetErrorMsg.restype = c_char_p
    lib.NET_DVR_PTZControl_Other.restype = BOOL
    lib.NET_DVR_PTZControlWithSpeed_Other.restype = BOOL
    lib.NET_DVR_StartRemoteConfig.restype = LONG
    lib.NET_DVR_StopRemoteConfig.restype = BOOL
    lib.NET_DVR_SendRemoteConfig.restype = BOOL


def setupSDK(sdk: CDLL, config: Optional[SDKConfig] = None):
    """
    Initialize the SDK. Must be called before any method of it is invoked. Remember to call shutdownSDK() to release its resources!.
    Optionally accepts a configuration dict.
    """

    logger.debug("Initializing SDK")
    sdk_init_result = sdk.NET_DVR_Init()
    if not sdk_init_result:
        raise RuntimeError("Unable to initialize SDK, init returned {}", sdk_init_result)

    if config:
        result = sdk.NET_DVR_SetLogToFile(config["log_level"].value, bytes(config["log_dir"], 'utf8'), False)
        if not result:
            raise RuntimeError("Cannot configure SDK logs, returned {}", result)

    valid_ip_result = sdk.NET_DVR_SetValidIP(0, True)
    if not valid_ip_result:
        logger.warning("SDK setValidIP returned {}", valid_ip_result)

    logger.debug("SDK initialized")


def shutdownSDK(sdk: CDLL):
    """Release the resources held by the SDK"""
    logger.debug("Shutting down SDK")
    sdk.NET_DVR_Cleanup()


def call_ISAPI(sdk: CDLL, user_id: int, http_method: str, url: str, requestBody: str = "") -> bytearray:
    """Call the specified ISAPI endpoint using the SDK.

    Args:
        sdk: an instance of Hikvision SDK
        user_id: the logged in user ID returned by the SDK
        http_method: HTTP method to use (e.g. GET, POST, PUT)
        url: The URL to invoke. Must start with `/ISAPI`
        requestBody: optional request body
    Returns:
        bytearray: Response body copied before SDK buffers are released
    """
    # Build the HTTP request string
    # e.g.: `GET /ISAPI/System/IO/outputs`
    inUrl = f"{http_method} {url}"

    logger.debug("Call ISAPI request method url body: {} {} {} ", http_method, url, requestBody)

    # Input information
    inputStruct = NET_DVR_XML_CONFIG_INPUT()

    requestUrlBuffer = bytes(inUrl, "ascii")
    inputStruct.lpRequestUrl = cast(c_char_p(requestUrlBuffer), c_void_p)
    inputStruct.dwRequestUrlLen = len(requestUrlBuffer)

    inputBuffer = bytes(requestBody, "ascii")

    inputStruct.lpInBuffer = cast(c_char_p(inputBuffer), c_void_p)
    inputStruct.dwInBufferSize = len(inputBuffer)

    inputStruct.dwSize = sizeof(inputStruct)

    # Output information
    outputStruct = NET_DVR_XML_CONFIG_OUTPUT()
    outputBufferSize = 1024 * 1024
    responseStatusBuffer = (c_char * outputBufferSize)()
    outputStruct.lpStatusBuffer = cast(responseStatusBuffer, c_void_p)
    outputStruct.dwStatusSize = outputBufferSize

    outputSize = (1024 * 1024)
    outputBuffer = (c_char * outputSize)()

    outputStruct.lpOutBuffer = cast(outputBuffer, c_void_p)
    outputStruct.dwOutBufferSize = outputSize
    outputStruct.dwSize = sizeof(outputStruct)

    # Do the actual call
    result = sdk.NET_DVR_STDXMLConfig(user_id, inputStruct, outputStruct)

    if not result:
        # The response status is populated only in case of error
        logger.debug("Response status: {}", responseStatusBuffer.value.decode("utf-8"))
        raise SDKError(sdk, f"Error while calling ISAPI {url}")

    size = outputStruct.dwReturnedXMLSize
    if size == 0 or not outputStruct.lpOutBuffer:
        response_body = bytearray()
    else:
        response_body = bytearray(string_at(outputStruct.lpOutBuffer, size))

    logger.debug("Response output: {}", response_body.decode("utf-8", errors="replace"))

    return response_body


def sdk_ptz_control_other(
    sdk: CDLL,
    user_id: int,
    channel: int,
    command: int,
    stop: bool = False,
    *,
    speed: int | None = None,
) -> None:
    """Send a PTZ command via NET_DVR_PTZControl_Other or WithSpeed variant."""
    dw_stop = 1 if stop else 0
    if speed is not None:
        result = sdk.NET_DVR_PTZControlWithSpeed_Other(user_id, channel, command, dw_stop, speed)
        api = "NET_DVR_PTZControlWithSpeed_Other"
    else:
        result = sdk.NET_DVR_PTZControl_Other(user_id, channel, command, dw_stop)
        api = "NET_DVR_PTZControl_Other"
    if not result:
        raise SDKError(sdk, f"{api} command {command} failed on channel {channel}")


# SDK alarm type to ISAPI-style eventType strings understood by parse_event_notification.
SDK_ALARM_TYPE_TO_EVENT_TYPE: dict[int, str] = {
    ALARMINFO_V30_ALARMTYPE_SEMAPHORE_ALARM: "IO",
    ALARMINFO_V30_ALARMTYPE_VIDEO_LOST: "VideoLoss",
    ALARMINFO_V30_ALARMTYPE_MOTION_DETECTION: "MotionDetection",
    ALARMINFO_V30_ALARMTYPE_TAMPERING_DETECTION: "TamperDetection",
    ALARMINFO_V30_ALARMTYPE_INTELLIGENT_SCENE_CHANGED: "SceneChangeDetection",
}

# VCA rule alarm (COMM_ALARM_RULE) event type mappings.
VCA_EVENT_TYPE_MAP: dict[int, str] = {
    0x1: "linedetection",
    0x2: "regionentrance",
    0x4: "regionexiting",
    0x8: "fielddetection",
}

VCA_EVENT_TYPE_EX_MAP: dict[int, str] = {
    1: "linedetection",
    2: "regionentrance",
    3: "regionexiting",
    4: "fielddetection",
    25: "linedetection",
}

# Known-noise SDK/VCA events that should be dropped without logging.
SDK_ALARM_TYPES_IGNORED = frozenset({ALARMINFO_V30_ALARMTYPE_ILLEGAL_ACCESS})
VCA_EVENT_TYPE_EX_IGNORED = frozenset({45})  # ENUM_VCA_EVENT_DURATION


def is_ignored_sdk_alarm_type(alarm_type: int) -> bool:
    """Return True for SDK alarm types that are intentionally not logged."""
    return alarm_type in SDK_ALARM_TYPES_IGNORED


def is_ignored_vca_rule_event(w_event_type_ex: int, dw_event_type: int) -> bool:
    """Return True for VCA rule events that are intentionally not logged."""
    return w_event_type_ex in VCA_EVENT_TYPE_EX_IGNORED


def map_sdk_alarm_type_to_event_type(alarm_type: int) -> str | None:
    """Map legacy SDK alarm type constants to an ISAPI eventType string."""
    return SDK_ALARM_TYPE_TO_EVENT_TYPE.get(alarm_type)


def build_sdk_alarm_raw_event(
    alarm_type: int,
    *,
    channel_id: int = 0,
    io_port_id: int = 0,
    alarm_output_number: int | None = None,
    relate_channel_id: int | None = None,
    disk_number: int | None = None,
) -> dict | None:
    """Build a subscribed-event dict for COMM_ALARM / COMM_ALARM_V30 callbacks."""
    event_type = map_sdk_alarm_type_to_event_type(alarm_type)
    if not event_type:
        return None
    raw_event = {
        "eventType": event_type,
        "alarmTypeID": alarm_type,
        "inputIOPortID": io_port_id,
        "channelID": channel_id,
        "videoInputChannelID": channel_id,
    }
    if alarm_output_number is not None:
        raw_event["outputIOPortID"] = alarm_output_number
    if relate_channel_id is not None:
        raw_event["relateChannelID"] = relate_channel_id
    if disk_number is not None:
        raw_event["diskNumber"] = disk_number
    return raw_event


def map_vca_rule_event(w_event_type_ex: int, dw_event_type: int) -> str | None:
    """Map NET_VCA_RULE_ALARM rule info to integration event id."""
    if w_event_type_ex:
        return VCA_EVENT_TYPE_EX_MAP.get(w_event_type_ex)
    return VCA_EVENT_TYPE_MAP.get(dw_event_type)


def decode_sdk_c_string(value) -> str:
    """Decode a fixed-size SDK char/byte buffer to a trimmed string."""
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    elif isinstance(value, Array):
        raw = bytes(value)
    elif isinstance(value, str):
        return value.strip("\x00").strip()
    else:
        return str(value).strip("\x00").strip()
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="ignore").strip()


def decode_sdk_license_plate(license_field) -> str:
    """Decode a fixed-size SDK license plate buffer to a trimmed string."""
    # return decode_sdk_c_string(license_field)
    if isinstance(license_field, (bytes, bytearray)):
        raw = bytes(license_field)
    elif isinstance(license_field, Array):
        raw = bytes(license_field)
    return raw.split(b"\x00", 1)[0].decode("gbk", errors="ignore").strip()

GATE_ALARM_TYPE_ILLEGAL_ENTRY = 0x01


def build_upload_plate_anpr_event(alarm_info) -> dict | None:
    """Map NET_DVR_PLATE_RESULT (COMM_UPLOAD_PLATE_RESULT) to an ANPR notification dict."""
    plate = alarm_info.struPlateInfo
    license_plate = decode_sdk_license_plate(plate.sLicense)
    if not license_plate:
        return None

    channel = alarm_info.byChanIndex or alarm_info.byDriveChan or 1

    return {
        "eventType": "ANPR",
        "channelID": channel,
        "ANPR": {
            "licensePlate": license_plate,
            "confidenceLevel": plate.byEntireBelieve,
            "plateColor": plate.byColor,
            "plateType": plate.byPlateType,
            "direction": alarm_info.byCarDirectionType,
        },
    }


def build_its_plate_anpr_event(alarm_info) -> dict | None:
    """Map NET_ITS_PLATE_RESULT (COMM_ITS_PLATE_RESULT) to an ANPR notification dict."""
    plate = alarm_info.struPlateInfo
    license_plate = decode_sdk_license_plate(plate.sLicense)
    if not license_plate:
        return None

    channel = (alarm_info.byChanIndexEx * 256) + alarm_info.byChanIndex
    if channel == 0:
        channel = alarm_info.byDriveChan or 1

    return {
        "eventType": "ANPR",
        "channelID": channel,
        "ANPR": {
            "licensePlate": license_plate,
            "confidenceLevel": plate.byEntireBelieve,
            "plateColor": plate.byColor,
            "plateType": plate.byPlateType,
        },
    }


def build_vehicle_control_anpr_event(alarm_info) -> dict | None:
    """Map NET_DVR_VEHICLE_CONTROL_ALARM to an ISAPI-style ANPR notification dict."""
    license_plate = decode_sdk_license_plate(alarm_info.sLicense)
    if not license_plate:
        return None
    return {
        "eventType": "ANPR",
        "channelID": alarm_info.dwChannel,
        "ANPR": {
            "licensePlate": license_plate,
            "plateType": alarm_info.byPlateType,
            "plateColor": alarm_info.byPlateColor,
            "listType": alarm_info.byListType,
        },
    }


def build_vehicle_control_list_dsalarm_event(alarm_info) -> dict:
    """Map NET_DVR_VEHICLE_CONTROL_LIST_DSALARM to an integration event payload.

    This callback indicates the device blocklist/allowlist changed and the client
    should sync via NET_DVR_GET_ALL_VEHICLE_CONTROL_LIST (cmd 3124). It does not
    carry a license plate detection.
    """
    return {
        "event_id": "vehicle_control_list_sync",
        "data_index": alarm_info.dwDataIndex,
        "operate_index": decode_sdk_c_string(alarm_info.sOperateIndex),
    }


def build_gate_alarm_anpr_event(alarm_info) -> dict | None:
    """Map NET_DVR_GATE_ALARMINFO (COMM_ITS_GATE_ALARMINFO) to an ANPR notification dict.

    Only illegal-entry alarms (byAlarmType=0x01) carry vehicle/plate data in the union.
    """
    if alarm_info.byAlarmType != GATE_ALARM_TYPE_ILLEGAL_ENTRY:
        return None

    vehicle = alarm_info.uAlarmInfo.struVehicleInfo
    license_plate = decode_sdk_license_plate(vehicle.sLicense)
    return {
        "eventType": "ANPR",
        "channelID": 0,
        "ANPR": {
            "licensePlate": license_plate,
            "vehicleType": vehicle.byVehicleType,
            "gateAlarmType": alarm_info.byAlarmType,
            "externalDevType": alarm_info.byExternalDevType,
            "externalDevStatus": alarm_info.byExternalDevStatus,
            "externalDevCtrlType": alarm_info.byExternalDevCtrlType,
        },
    }


def read_sdk_alarm_image_buffer(
    buffer_ptr: c_void_p | int | None,
    buffer_len: int,
    upload_type: int = 0,
) -> bytes | None:
    """Copy image bytes from an SDK alarm callback buffer before it is released."""
    if not buffer_len or not buffer_ptr:
        return None
    if upload_type == 1:
        return None
    return bytes(string_at(buffer_ptr, buffer_len))


def read_anpr_image_from_plate_result(alarm_info) -> bytes | None:
    """Return scene or plate-crop JPEG from NET_DVR_PLATE_RESULT (COMM_UPLOAD_PLATE_RESULT)."""
    scene = read_sdk_alarm_image_buffer(alarm_info.pBuffer1, alarm_info.dwPicLen, 0)
    if scene:
        return scene
    return read_sdk_alarm_image_buffer(alarm_info.pBuffer2, alarm_info.dwPicPlateLen, 0)


def read_anpr_image_from_vehicle_control(alarm_info) -> bytes | None:
    """Return JPEG from NET_DVR_VEHICLE_CONTROL_ALARM when uploaded inline."""
    return read_sdk_alarm_image_buffer(
        alarm_info.pPicData,
        alarm_info.dwPicDataLen,
        alarm_info.byPicTransType,
    )


def read_acs_face_image_from_alarm(alarm_info) -> bytes | None:
    """Return JPEG from NET_DVR_ACS_ALARM_INFO when uploaded inline."""
    return read_sdk_alarm_image_buffer(
        alarm_info.pPicData,
        alarm_info.dwPicDataLen,
        getattr(alarm_info, "byPicTransType", 0),
    )


def _struct_field_key(field: str) -> str:
    if field.startswith("dw"):
        return field[2:]
    if field.startswith("by"):
        return field[2:]
    if field.startswith("stru"):
        return field[4:]
    if field.startswith("u"):
        return field[1:]
    if field.startswith("s"):
        return field[1:]
    if field.startswith("f") or field.startswith("w"):
        return field[1:]
    return field


def _struct_value_to_python(val, field: str, depth: int):
    if isinstance(val, Structure):
        return structToDict(val, depth + 1) if depth < 5 else repr(val)
    if isinstance(val, Union):
        return structToDict(val, depth + 1) if depth < 5 else repr(val)
    if isinstance(val, Array):
        if val._type_ == c_byte and field.startswith("s"):
            return c_char_p(addressof(val)).value
        if val._type_ == c_byte and field.startswith("by"):
            return bytearray(val)
        return list(val)
    if isinstance(val, c_void_p):
        return None if not val else int(val)
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace") if field.startswith("s") else val
    if hasattr(val, "value") and type(val).__module__ == "ctypes":
        return val.value
    return val


def structToDict(stru, depth=0):
    if stru is None:
        return None
    if not hasattr(stru, "_fields_"):
        return repr(stru)
    data = {}
    for field, _ in stru._fields_:
        if field.startswith("byRes"):
            continue
        val = getattr(stru, field)
        data[_struct_field_key(field)] = _struct_value_to_python(val, field, depth)
    return data


def format_struct_dump(stru, *, width: int = 120) -> str:
    """Pretty-print an SDK ctypes structure for debug logging."""
    return pprint.pformat(structToDict(stru), width=width, sort_dicts=False, compact=False)

class SDKError(RuntimeError):
    """
    This exception should be appropriately trapped and explained to the user where it is raised.
    """
    def __init__(self, sdk: CDLL, user_message: str, *args: object) -> None:
        """Base exception class for error generating from the SDK API

        Use the `user_message` parameter to a user-friendly message that will be printed out along with the error.
        It automatically extracts the error code and message from the SDK.
        This class sets the `error_code` and `error_message` as a tuple inside its `args` property.
        """
        super().__init__(*args)
        error_code = sdk.NET_DVR_GetLastError()
        error_message: str = sdk.NET_DVR_GetErrorMsg(c_long(error_code)).decode('utf-8')

        # Prepend the three parameters to the rest of the tuple in args
        self.args = (user_message, error_code, error_message, *self.args)

