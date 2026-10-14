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
EVENT_THERMAL: Final = "thermal"
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
    "thermometry": {
        "type": EVENT_THERMAL,
        "label": "Thermometry",
        "slug": "thermometry/basicParam",
        "direct_node": "ThermometryBasicParam",
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
    "shelteralarm": "tamperdetection",
    "VMDHumanVehicle": "motiondetection",
}

MUTEX_ALTERNATE_ID = {"motiondetection": "VMDHumanVehicle"}

# Security control panel (SecurityCP) partition arming modes
PARTITION_ARM_STAY: Final = "stay"
PARTITION_ARM_AWAY: Final = "away"

# Security control panel peripheral kinds (Peripheral.kind), and the SecurityCP/status/*
# endpoint + list/item JSON keys used to fetch each one. Gated behind the matching
# isSpt*Mod/isSptRemoteStatus flag from SecurityCP/status/capabilities, since not every
# panel model/firmware exposes every kind (e.g. repeaters only exist on fully-wireless
# AX PRO panels, not the wired-first AX Hybrid PRO).
PERIPHERAL_KEYPAD: Final = "keypad"
PERIPHERAL_EXTENSION_MODULE: Final = "extension_module"
PERIPHERAL_SIREN: Final = "siren"
PERIPHERAL_REMOTE: Final = "remote"
PERIPHERAL_REPEATER: Final = "repeater"

# (capability flag, status endpoint, response list key, response item key, kind)
PERIPHERAL_ENDPOINTS = [
    ("isSptKeypadMod", "SecurityCP/status/keypadStatus", "KeypadList", "Keypad", PERIPHERAL_KEYPAD),
    (
        "isSptExtensionModuleMod",
        "SecurityCP/status/extensionModuleStatus",
        "ExtensionList",
        "ExtensionModule",
        PERIPHERAL_EXTENSION_MODULE,
    ),
    ("isSptSirenMod", "SecurityCP/status/sirenStatus", "SirenList", "Siren", PERIPHERAL_SIREN),
    ("isSptRemoteStatus", "SecurityCP/status/remoteStatus", "RemoteList", "Remote", PERIPHERAL_REMOTE),
    ("isSptRepeaterMod", "SecurityCP/status/repeaterStatus", "RepeaterList", "Repeater", PERIPHERAL_REPEATER),
]

# Generic zone detector categories, mapped from SecurityCP zone "detectorType" values.
# Kept HA-agnostic here; the integration layer maps these to BinarySensorDeviceClass.
ZONE_CATEGORY_DOOR: Final = "door"
ZONE_CATEGORY_MOTION: Final = "motion"
ZONE_CATEGORY_SMOKE: Final = "smoke"
ZONE_CATEGORY_GAS: Final = "gas"
ZONE_CATEGORY_MOISTURE: Final = "moisture"
ZONE_CATEGORY_HEAT: Final = "heat"
ZONE_CATEGORY_SAFETY: Final = "safety"
ZONE_CATEGORY_VIBRATION: Final = "vibration"
ZONE_CATEGORY_TAMPER: Final = "tamper"

ZONE_DETECTOR_CATEGORY = {
    "magneticContact": ZONE_CATEGORY_DOOR,
    "slimMagneticContact": ZONE_CATEGORY_DOOR,
    "magnetShockDetector": ZONE_CATEGORY_DOOR,
    "singleInfraredDetector": ZONE_CATEGORY_DOOR,
    "passiveInfraredDetector": ZONE_CATEGORY_MOTION,
    "curtainInfraredDetector": ZONE_CATEGORY_MOTION,
    "indoorDualTechnologyDetector": ZONE_CATEGORY_MOTION,
    "dualTechnologyPirDetector": ZONE_CATEGORY_MOTION,
    "tripleTechnologyPirDetector": ZONE_CATEGORY_MOTION,
    "activeInfraredDetector": ZONE_CATEGORY_MOTION,
    "wirelessPIRCeilingDetector": ZONE_CATEGORY_MOTION,
    "wirelessPIRCurtainDetector": ZONE_CATEGORY_MOTION,
    "wirelessDTAMCurtainDetector": ZONE_CATEGORY_MOTION,
    "outdoorDetector": ZONE_CATEGORY_MOTION,
    "smokeDetector": ZONE_CATEGORY_SMOKE,
    "wirelessSmokeDetector": ZONE_CATEGORY_SMOKE,
    "combustibleGasDetector": ZONE_CATEGORY_GAS,
    "wirelessCODetector": ZONE_CATEGORY_GAS,
    "waterDetector": ZONE_CATEGORY_MOISTURE,
    "waterLeakDetector": ZONE_CATEGORY_MOISTURE,
    "humidityDetector": ZONE_CATEGORY_MOISTURE,
    "glassBreakDetector": ZONE_CATEGORY_SAFETY,
    "wirelessGlassBreakDetector": ZONE_CATEGORY_SAFETY,
    "panicButton": ZONE_CATEGORY_SAFETY,
    "vibrationDetector": ZONE_CATEGORY_VIBRATION,
    "tamperDetector": ZONE_CATEGORY_TAMPER,
    "wirelessHeatDetector": ZONE_CATEGORY_HEAT,
    "temperatureDetector": ZONE_CATEGORY_HEAT,
}
