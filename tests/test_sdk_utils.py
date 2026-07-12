"""Tests for SDK utility helpers."""

from custom_components.hikvision_next.sdk.utils import map_vca_rule_event


def test_map_vca_rule_event_dw_event_type():
    assert map_vca_rule_event(0, 0x1) == "linedetection"
    assert map_vca_rule_event(0, 0x2) == "regionentrance"
    assert map_vca_rule_event(0, 0x4) == "regionexiting"
    assert map_vca_rule_event(0, 0x8) == "fielddetection"


def test_map_vca_rule_event_w_event_type_ex():
    assert map_vca_rule_event(1, 0) == "linedetection"
    assert map_vca_rule_event(2, 0) == "regionentrance"
    assert map_vca_rule_event(3, 0) == "regionexiting"
    assert map_vca_rule_event(4, 0) == "fielddetection"


def test_map_vca_rule_event_unknown():
    assert map_vca_rule_event(0, 0x10) is None
    assert map_vca_rule_event(99, 0) is None