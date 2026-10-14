"""hikvision integration constants."""

from typing import Final

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from .isapi.const import EVENTS as ISAPI_EVENTS
from .isapi.const import ZONE_DETECTOR_CATEGORY

DOMAIN: Final = "hikvision_next"

RTSP_PORT_FORCED: Final = "rtsp_port_forced"
CONF_SET_ALARM_SERVER: Final = "set_alarm_server"
CONF_ALARM_SERVER_HOST: Final = "alarm_server"
ALARM_SERVER_PATH = "/api/hikvision"
# Overrides the primary status coordinator's poll interval (SecurityCoordinator for security
# panels, EventsCoordinator for NVR/DVR/cameras). Seconds; 0 disables automatic polling
# entirely (push/manual refresh only). Left unset, each device type keeps its own default.
CONF_POLLING_INTERVAL: Final = "polling_interval"

EVENTS_COORDINATOR: Final = "events"
SECONDARY_COORDINATOR: Final = "secondary"
SECURITY_COORDINATOR: Final = "security"
HOLIDAY_MODE = "holiday_mode"
SECURITY_HOST_STATUS = "security_host_status"

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
    "thermometry": {
        **ISAPI_EVENTS["thermometry"],
        "device_class": BinarySensorDeviceClass.HEAT,
    },
}

ZONE_CATEGORY_DEVICE_CLASS = {
    "door": BinarySensorDeviceClass.DOOR,
    "motion": BinarySensorDeviceClass.MOTION,
    "smoke": BinarySensorDeviceClass.SMOKE,
    "gas": BinarySensorDeviceClass.GAS,
    "moisture": BinarySensorDeviceClass.MOISTURE,
    "heat": BinarySensorDeviceClass.HEAT,
    "safety": BinarySensorDeviceClass.SAFETY,
    "vibration": BinarySensorDeviceClass.VIBRATION,
    "tamper": BinarySensorDeviceClass.TAMPER,
}

ZONE_DEVICE_CLASS = {
    detector_type: ZONE_CATEGORY_DEVICE_CLASS[category]
    for detector_type, category in ZONE_DETECTOR_CATEGORY.items()
}
