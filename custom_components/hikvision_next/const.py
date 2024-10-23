"""hikvision integration constants."""

from typing import Final

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

DOMAIN: Final = "hikvision_next"

DATA_SET_ALARM_SERVER: Final = "set_alarm_server"
DATA_ALARM_SERVER_HOST: Final = "alarm_server"
DATA_ISAPI = "isapi"
ALARM_SERVER_PATH = "/api/hikvision"

EVENTS_COORDINATOR: Final = "events"
SECONDARY_COORDINATOR: Final = "secondary"
HOLIDAY_MODE = "holiday_mode"

CONNECTION_TYPE_DIRECT = "Direct"
CONNECTION_TYPE_PROXIED = "Proxied"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ACTION_REBOOT = "reboot"
ACTION_ISAPI_REQUEST = "isapi_request"
ACTION_UPDATE_SNAPSHOT = "update_snapshot"

HIKVISION_EVENT = f"{DOMAIN}_event"
EVENT_BASIC: Final = "basic"
EVENT_IO: Final = "io"
EVENT_SMART: Final = "smart"
EVENT_PIR: Final = "pir"
EVENTS = {
    "motiondetection": {
        "type": EVENT_BASIC,
        "label": "Motion",
        "slug": "motionDetection",
        "mutex": True,
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "tamperdetection": {
        "type": EVENT_BASIC,
        "label": "Video Tampering",
        "slug": "tamperDetection",
        "device_class": BinarySensorDeviceClass.TAMPER,
    },
    "videoloss": {
        "type": EVENT_BASIC,
        "label": "Video Loss",
        "slug": "videoLoss",
        "device_class": BinarySensorDeviceClass.PROBLEM,
    },
    "scenechangedetection": {
        "type": EVENT_SMART,
        "label": "Scene Change",
        "slug": "SceneChangeDetection",
        "mutex": True,
        "device_class": BinarySensorDeviceClass.TAMPER,
    },
    "fielddetection": {
        "type": EVENT_SMART,
        "label": "Intrusion",
        "slug": "FieldDetection",
        "mutex": True,
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "linedetection": {
        "type": EVENT_SMART,
        "label": "Line Crossing",
        "slug": "LineDetection",
        "mutex": True,
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "regionentrance": {
        "type": EVENT_SMART,
        "label": "Region Entrance",
        "slug": "regionEntrance",
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "regionexiting": {
        "type": EVENT_SMART,
        "label": "Region Exiting",
        "slug": "regionExiting",
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "io": {
        "type": EVENT_IO,
        "label": "Alarm Input",
        "slug": "inputs",
        "direct_node": "IOInputPort",
        "proxied_node": "IOProxyInputPort",
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "pir": {
        "type": EVENT_PIR,
        "label": "PIR",
        "slug": "WLAlarm/PIR",
        "direct_node": "PIRAlarm",
        "device_class": BinarySensorDeviceClass.MOTION,
    }
}

EVENTS_ALTERNATE_ID = {
    "vmd": "motiondetection",
    "shelteralarm": "tamperdetection",
    "VMDHumanVehicle": "motiondetection",
}

MUTEX_ALTERNATE_IDS = {"motiondetection": "VMDHumanVehicle"}

STREAM_TYPE = {
    1: "Main Stream",
    2: "Sub-stream",
    3: "Third Stream",
    4: "Transcoded Stream",
}
