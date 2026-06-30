"""Tests for scripts/nvr_event_triggers.py helpers."""

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "nvr_event_triggers.py"
spec = importlib.util.spec_from_file_location("nvr_event_triggers", SCRIPT_PATH)
nvr_event_triggers = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["nvr_event_triggers"] = nvr_event_triggers
spec.loader.exec_module(nvr_event_triggers)


def test_event_type_candidates_anpr():
    candidates = nvr_event_triggers.event_type_candidates("ANPR")
    assert candidates[0] == "ANPR"
    assert "vehicleDetect" in candidates


def test_event_type_matches_alias():
    assert nvr_event_triggers.event_type_matches("ANPR", "vehicleDetect")
    assert nvr_event_triggers.event_type_matches("vmd", "VMD")
    assert not nvr_event_triggers.event_type_matches("ANPR", "VMD")


def test_event_type_matches_exact():
    assert nvr_event_triggers.event_type_matches("ANPR", "ANPR", exact=True)
    assert not nvr_event_triggers.event_type_matches("ANPR", "vehicledetection", exact=True)


def test_parse_channel_event_type_options():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <ChannelEventCap xmlns="http://www.isapi.org/ver20/XMLSchema">
      <eventType opt="VMD,ANPR,fielddetection"/>
    </ChannelEventCap>"""
    options = nvr_event_triggers.parse_channel_event_type_options(xml)
    assert options == ["VMD", "ANPR", "fielddetection"]


def test_build_new_trigger_dyn_channel():
    trigger = nvr_event_triggers.build_new_trigger(
        channel=43,
        event_type="ANPR",
        channel_field="dynVideoInputChannelID",
        enabled=True,
    )
    assert trigger["id"] == "ANPR-43"
    assert trigger["eventType"] == "ANPR"
    assert trigger["dynVideoInputChannelID"] == "43"
    assert nvr_event_triggers.has_center(trigger)


def test_channel_binding_field_prefers_existing_trigger():
    triggers = [
        {"id": "VMD-43", "eventType": "VMD", "dynVideoInputChannelID": "43"},
    ]
    assert nvr_event_triggers.channel_binding_field(triggers, 43) == "dynVideoInputChannelID"


def test_set_center_enable_keeps_existing_center():
    trigger = {
        "id": "vehicledetection-43",
        "EventTriggerNotificationList": {
            "EventTriggerNotification": [
                {"id": "record-43", "notificationMethod": "record"},
                {"id": "center", "notificationMethod": "center"},
            ]
        },
    }
    updated = nvr_event_triggers.set_center(trigger, enabled=True)
    methods = nvr_event_triggers.notification_methods(updated)
    assert "center" in methods
    assert "record" in methods