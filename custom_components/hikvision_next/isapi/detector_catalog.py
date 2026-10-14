"""Friendly names for security control panel zone detector models and types.

Hikvision's own ISAPI reports a zone's detector either by an internal hex model code (e.g.
"0x000F6") or, when no code is known/returned, by the raw detectorType string (e.g.
"passiveInfraredDetector"). Neither is meant for end users -- this maps both to something
readable for the HA device page's "Model" field.

To add a device you've identified: add its hex code (exactly as your panel reports it,
case included) to ZONE_MODEL_NAMES below, mapped to the real Hikvision SKU. Nothing else
needs to change -- new entries take effect on the next poll/reload.
"""

from __future__ import annotations

# Hex model code (SecurityCP/status/zones' "model" field) -> real Hikvision SKU.
ZONE_MODEL_NAMES: dict[str, str] = {
    "0x000F6": "DS-PDMC-EG2-WB(B)",  # wireless magnetic contact (door/window)
    "0x93021": "DS-PM1-RT-HWB",  # wireless PIR motion detector
}

# Fallback when the model code above is unknown/missing: detectorType -> a short
# human-readable description. Written without "wired"/"wireless" -- that's prefixed
# dynamically from the zone's own zoneAttrib in friendly_zone_model() below.
DETECTOR_TYPE_NAMES: dict[str, str] = {
    "magneticContact": "Magnetic Contact",
    "slimMagneticContact": "Slim Magnetic Contact",
    "magnetShockDetector": "Magnetic Shock Detector",
    "singleInfraredDetector": "Infrared Detector",
    "passiveInfraredDetector": "Infrared Detector",
    "curtainInfraredDetector": "Infrared Curtain Detector",
    "indoorDualTechnologyDetector": "Dual-Technology Detector",
    "dualTechnologyPirDetector": "Dual-Technology Detector",
    "tripleTechnologyPirDetector": "Triple-Technology Detector",
    "activeInfraredDetector": "Active Infrared Detector",
    "wirelessPIRCeilingDetector": "Ceiling PIR Detector",
    "wirelessPIRCurtainDetector": "Curtain PIR Detector",
    "wirelessDTAMCurtainDetector": "Curtain Detector",
    "outdoorDetector": "Outdoor Detector",
    "smokeDetector": "Smoke Detector",
    "wirelessSmokeDetector": "Smoke Detector",
    "combustibleGasDetector": "Gas Detector",
    "wirelessCODetector": "CO Detector",
    "waterDetector": "Water Detector",
    "waterLeakDetector": "Water Leak Detector",
    "humidityDetector": "Humidity Detector",
    "glassBreakDetector": "Glass-Break Detector",
    "wirelessGlassBreakDetector": "Glass-Break Detector",
    "panicButton": "Panic Button",
    "vibrationDetector": "Vibration Detector",
    "tamperDetector": "Tamper Detector",
    "wirelessHeatDetector": "Heat Detector",
    "temperatureDetector": "Temperature Detector",
    "other": "Detector",
}

_ZONE_ATTRIB_PREFIX = {"wired": "Wired", "wireless": "Wireless"}


def friendly_zone_model(model: str | None, detector_type: str, zone_attrib: str | None) -> str:
    """Return a human-readable model/description for a zone's HA device page.

    Prefers a known real SKU for the reported model code (ZONE_MODEL_NAMES); falls back to
    a short description of the detector type, prefixed with "Wired"/"Wireless" when known
    (DETECTOR_TYPE_NAMES); and finally to the raw model code or detector type verbatim if
    neither is recognized yet -- so an unknown device stays visible/reportable rather than
    silently disappearing behind a KeyError or a blank field.
    """
    if model:
        return ZONE_MODEL_NAMES.get(model, model)

    name = DETECTOR_TYPE_NAMES.get(detector_type, detector_type)
    prefix = _ZONE_ATTRIB_PREFIX.get(zone_attrib)
    return f"{prefix} {name}" if prefix else name
