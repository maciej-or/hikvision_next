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
EVENT_TRAFFIC: Final = "trafic"

SUBSCRIBE_ENDPOINT: Final = "Event/notification/subscribeEvent"

# Face events are used for capability discovery and the face snap image entity only.
FACE_SNAP_EVENT_IDS: Final = frozenset({"facesnap", "facecontrast", "facedetection"})

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
    "anpr": {
        "type": EVENT_TRAFFIC,
        "label": "License Plate Recognition",
        "slug": "vehicleDetect",
        "direct_node": "VehicleDetectCfg",
    },
    "facesnap": {
        "type": EVENT_BASIC,
        "label": "Face Snap",
        "slug": "faceSnap",
    },
    "facecontrast": {
        "type": EVENT_BASIC,
        "label": "Face Contrast",
        "slug": "faceContrast",
    },
    "facedetection": {
        "type": EVENT_BASIC,
        "label": "Face Detection",
        "slug": "faceDetection",
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
    "vmdhumanvehicle": "motiondetection",
    "vehicledetection": "anpr",
    "anpr": "anpr",
    "facesnap": "facesnap",
    "facecapture": "facesnap",
    "facecontrast": "facecontrast",
    "facedetection": "facedetection",
}

# Event/triggers/{prefix}-{channel} path variants (order matters: NVR commonly uses VMD).
EVENT_TRIGGER_PREFIXES: Final = {
    "motiondetection": [
        "VMD",
        "motionDetection",
        "motiondetection",
        "vmd",
        "thermometry",
        "VMDHumanVehicle",
    ],
    "tamperdetection": ["tamperdetection", "Shelteralarm", "shelteralarm"],
    "fielddetection": ["fielddetection", "fieldDetection"],
    "linedetection": ["linedetection", "lineDetection"],
    "regionentrance": ["regionentrance", "regionEntrance"],
    "regionexiting": ["regionexiting", "regionExiting"],
    "facesnap": ["faceSnap", "faceCapture", "facesnap"],
    "facecontrast": ["faceContrast", "facecontrast"],
    "facedetection": ["faceDetection", "facedetection"],
}

MUTEX_ALTERNATE_ID = {"motiondetection": "VMDHumanVehicle"}
