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

EVENT_SWITCH_LABEL_FORMAT = "{} Detection"
HOLIDAY_MODE_SWITCH_LABEL = "Holiday mode"
ALARM_SERVER_SENSOR_LABEL_FORMAT = "Alarm Server {}"

DEVICE_TYPE_NVR = ["NVR", "DVR"]
DEVICE_TYPE_IP_CAMERA = "IPCamera"
DEVICE_TYPE_ANALOG_CAMERA = "AnalogCamera"

EVENT_BASIC: Final = "basic"
EVENT_SMART: Final = "smart"
EVENTS = {
    "motiondetection": {
        "type": EVENT_BASIC,
        "label": "Motion",
        "slug": "motionDetection",
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
        "device_class": BinarySensorDeviceClass.TAMPER,
    },
    "fielddetection": {
        "type": EVENT_SMART,
        "label": "Intrusion",
        "slug": "FieldDetection",
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "linedetection": {
        "type": EVENT_SMART,
        "label": "Line Crossing",
        "slug": "LineDetection",
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
