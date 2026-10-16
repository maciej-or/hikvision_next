"""Tests for SDK structure pretty-printing."""

from custom_components.hikvision_next.sdk.hcnetsdk import NET_DVR_PLATE_RESULT, NET_VCA_RULE_ALARM
from custom_components.hikvision_next.sdk.utils import format_struct_dump, structToDict


def test_struct_to_dict_converts_uint_array_union_fields():
    alarm = NET_VCA_RULE_ALARM()
    alarm.struRuleInfo.byRuleID = 3
    alarm.struRuleInfo.wEventTypeEx = 42
    alarm.struRuleInfo.dwEventType = 7

    data = structToDict(alarm)
    assert data["RuleInfo"]["RuleID"] == 3
    assert data["RuleInfo"]["EventTypeEx"] == 42
    assert isinstance(data["RuleInfo"]["EventParam"]["Len"], list)
    assert len(data["RuleInfo"]["EventParam"]["Len"]) == 23


def test_format_struct_dump_for_rule_alarm_is_readable():
    alarm = NET_VCA_RULE_ALARM()
    alarm.struRuleInfo.byRuleID = 1
    text = format_struct_dump(alarm)
    assert "RuleInfo" in text
    assert "EventParam" in text
    assert "Len" in text


def test_format_struct_dump_for_plate_result():
    plate = NET_DVR_PLATE_RESULT()
    plate.struPlateInfo.sLicense = b"ABC123\x00"
    text = format_struct_dump(plate)
    assert "PlateInfo" in text
    assert "ABC123" in text or "b'ABC123" in text