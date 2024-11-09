from typing import Final

GET = "GET"
PUT = "PUT"
POST = "POST"

CONNECTION_TYPE_DIRECT = "Direct"
CONNECTION_TYPE_PROXIED = "Proxied"

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
    },
    "tamperdetection": {
        "type": EVENT_BASIC,
        "label": "Video Tampering",
        "slug": "tamperDetection",
    },
    "videoloss": {
        "type": EVENT_BASIC,
        "label": "Video Loss",
        "slug": "videoLoss",
    },
    "scenechangedetection": {
        "type": EVENT_SMART,
        "label": "Scene Change",
        "slug": "SceneChangeDetection",
        "mutex": True,
    },
    "fielddetection": {
        "type": EVENT_SMART,
        "label": "Intrusion",
        "slug": "FieldDetection",
        "mutex": True,
    },
    "linedetection": {
        "type": EVENT_SMART,
        "label": "Line Crossing",
        "slug": "LineDetection",
        "mutex": True,
    },
    "regionentrance": {
        "type": EVENT_SMART,
        "label": "Region Entrance",
        "slug": "regionEntrance",
    },
    "regionexiting": {
        "type": EVENT_SMART,
        "label": "Region Exiting",
        "slug": "regionExiting",
    },
    "io": {
        "type": EVENT_IO,
        "label": "Alarm Input",
        "slug": "inputs",
        "direct_node": "IOInputPort",
        "proxied_node": "IOProxyInputPort",
    },
    "pir": {
        "type": EVENT_PIR,
        "label": "PIR",
        "slug": "WLAlarm/PIR",
        "direct_node": "PIRAlarm",
    },
}

STREAM_TYPE = {
    1: "Main Stream",
    2: "Sub-stream",
    3: "Third Stream",
    4: "Transcoded Stream",
}


EVENTS_ALTERNATE_ID = {
    "vmd": "motiondetection",
    "thermometry": "motiondetection",
    "shelteralarm": "tamperdetection",
    "VMDHumanVehicle": "motiondetection",
}

MUTEX_ALTERNATE_ID = {"motiondetection": "VMDHumanVehicle"}
