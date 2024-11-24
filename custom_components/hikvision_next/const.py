"""hikvision integration constants."""

from typing import Final

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from .isapi.const import EVENTS as ISAPI_EVENTS

DOMAIN: Final = "hikvision_next"

RTSP_PORT_FORCED: Final = "rtsp_port_forced"
CONF_SET_ALARM_SERVER: Final = "set_alarm_server"
CONF_ALARM_SERVER_HOST: Final = "alarm_server"
ALARM_SERVER_PATH = "/api/hikvision"

EVENTS_COORDINATOR: Final = "events"
SECONDARY_COORDINATOR: Final = "secondary"
HOLIDAY_MODE = "holiday_mode"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ACTION_REBOOT = "reboot"
ACTION_ISAPI_REQUEST = "isapi_request"
ACTION_UPDATE_SNAPSHOT = "update_snapshot"

HIKVISION_EVENT = f"{DOMAIN}_event"

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
}
