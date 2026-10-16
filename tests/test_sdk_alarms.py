"""Tests for SDK alarm and VCA rule event mapping."""

from custom_components.hikvision_next.isapi import ISAPIClient
from custom_components.hikvision_next.sdk.hcnetsdk import ALARMINFO_V30_ALARMTYPE_ILLEGAL_ACCESS
from custom_components.hikvision_next.sdk.utils import (
    build_sdk_alarm_raw_event,
    is_ignored_sdk_alarm_type,
    is_ignored_vca_rule_event,
    map_vca_rule_event,
)


def test_build_sdk_alarm_raw_event_maps_motion_detection():
    raw_event = build_sdk_alarm_raw_event(3, channel_id=1, io_port_id=0)
    assert raw_event is not None
    assert raw_event["eventType"] == "MotionDetection"
    assert raw_event["channelID"] == 1


def test_build_sdk_alarm_raw_event_ignores_unsupported_alarm_type():
    assert build_sdk_alarm_raw_event(ALARMINFO_V30_ALARMTYPE_ILLEGAL_ACCESS) is None


def test_parse_event_notification_ignores_generic_alarm_info():
    assert ISAPIClient.parse_event_notification({"eventType": "alarmInfo", "channelID": 1}) is None


def test_map_vca_rule_event_duration_is_unmapped():
    assert map_vca_rule_event(45, 0) is None


def test_map_vca_rule_event_advanced_line_crossing():
    assert map_vca_rule_event(25, 0) == "linedetection"


def test_ignored_sdk_and_vca_noise_events():
    assert is_ignored_sdk_alarm_type(ALARMINFO_V30_ALARMTYPE_ILLEGAL_ACCESS)
    assert not is_ignored_sdk_alarm_type(3)
    assert is_ignored_vca_rule_event(45, 0)
    assert not is_ignored_vca_rule_event(25, 0)