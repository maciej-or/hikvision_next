"""hikvision integration constants."""

from typing import Any, Final

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from .isapi.const import EVENTS as ISAPI_EVENTS, FACE_SNAP_EVENT_IDS

DOMAIN: Final = "hikvision_next"

RTSP_PORT_FORCED: Final = "rtsp_port_forced"
CONF_CONNECTION_TYPE: Final = "connection_type"
CONF_USE_HTTP_NOTIFY: Final = "use_http_notify"
CONF_SET_ALARM_SERVER: Final = "set_alarm_server"
CONF_ALARM_SERVER_HOST: Final = "alarm_server"
ALARM_SERVER_PATH = "/api/hikvision"

CONF_CONNECTION_HTTP_NOTIFY = "http_notify"
CONF_CONNECTION_HTTP_CALLBACK = "http_callback"
CONF_CONNECTION_SDK = "sdk"


def resolve_connection_type(data: dict[str, Any]) -> str:
    """Resolve connection type from config data, migrating legacy use_http_notify."""
    if CONF_CONNECTION_TYPE in data:
        return data[CONF_CONNECTION_TYPE]
    if CONF_USE_HTTP_NOTIFY in data:
        return CONF_CONNECTION_HTTP_NOTIFY if data[CONF_USE_HTTP_NOTIFY] else CONF_CONNECTION_HTTP_CALLBACK
    return CONF_CONNECTION_HTTP_NOTIFY


EVENTS_COORDINATOR: Final = "events"
SECONDARY_COORDINATOR: Final = "secondary"
HOLIDAY_MODE = "holiday_mode"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ACTION_REBOOT = "reboot"
ACTION_ISAPI_REQUEST = "isapi_request"
ACTION_UPDATE_SNAPSHOT = "update_snapshot"
ACTION_PTZ_MOVE = "ptz_move"
ACTION_PTZ_STOP = "ptz_stop"
ACTION_INTERCOM_ANSWER = "intercom_answer"
ACTION_INTERCOM_CALL = "intercom_call"
ACTION_INTERCOM_REJECT = "intercom_reject"
ACTION_INTERCOM_HANGUP = "intercom_hangup"

HIKVISION_EVENT = f"{DOMAIN}_event"

# Device-level events: channel_id stays 0 (not remapped to a camera channel).
DEVICE_LEVEL_EVENT_IDS: Final = frozenset(
    {"door", "lock", "face", "anpr", "doorbell", "calling", "intercom"}
)

# Events exposed as text sensors (not binary sensors).
TEXT_SENSOR_EVENT_IDS: Final = frozenset({"face"})

# Events exposed as lock entities (not binary sensors or switches).
LOCK_EVENT_IDS: Final = frozenset({"lock"})

ANPR_LICENSE_PLATE_SENSOR_SUFFIX: Final = "anpr_plate"
ANPR_IMAGE_SUFFIX: Final = "anpr_snap"
FACE_VERIFY_IMAGE_SUFFIX: Final = "face_verify"
FACE_PERSON_PULSE_SECONDS: Final = 1.0

EVENTS = {
    "motiondetection": {
        **ISAPI_EVENTS["motiondetection"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "tamperdetection": {
        **ISAPI_EVENTS["tamperdetection"],
        "device_class": BinarySensorDeviceClass.TAMPER,
    },
    "videoloss": {
        **ISAPI_EVENTS["videoloss"],
        "device_class": BinarySensorDeviceClass.PROBLEM,
    },
    "scenechangedetection": {
        **ISAPI_EVENTS["scenechangedetection"],
        "device_class": BinarySensorDeviceClass.TAMPER,
    },
    "fielddetection": {
        **ISAPI_EVENTS["fielddetection"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "linedetection": {
        **ISAPI_EVENTS["linedetection"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "regionentrance": {
        **ISAPI_EVENTS["regionentrance"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "regionexiting": {
        **ISAPI_EVENTS["regionexiting"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "io": {
        **ISAPI_EVENTS["io"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "pir": {
        **ISAPI_EVENTS["pir"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "anpr": {
        **ISAPI_EVENTS["anpr"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "door": {
        **ISAPI_EVENTS["io"],
        "mutex": True,
        "device_class": BinarySensorDeviceClass.DOOR,
    },
    "lock": {
        **ISAPI_EVENTS["io"],
        "mutex": True,
        "icon": "mdi:lock",
    },
    "face": {
        **ISAPI_EVENTS["io"],
        "icon": "mdi:human",
        "name": "Face",
        "mutex": True
    },
    "doorbell": {
        "icon": "mdi:doorbell",
        "mutex": True,
    },
    "calling": {
        "icon": "mdi:phone-ring",
        "mutex": True,
    },
    "intercom": {
        "icon": "mdi:phone-in-talk",
        "mutex": True,
    },
}
