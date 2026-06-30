"""Tests for SDK vehicle control ANPR helpers."""

from custom_components.hikvision_next.isapi import ISAPIClient
from custom_components.hikvision_next.sdk.utils import (
    GATE_ALARM_TYPE_ILLEGAL_ENTRY,
    build_gate_alarm_anpr_event,
    build_vehicle_control_anpr_event,
    build_vehicle_control_list_dsalarm_event,
    decode_sdk_c_string,
    decode_sdk_license_plate,
)
from custom_components.hikvision_next.sdk.vehicle_control_list import (
    LIST_TYPE_NAMES,
    build_lp_list_audit_search_xml,
    build_vehicle_control_cond,
    is_sdk_vehicle_list_unsupported,
    parse_lp_list_audit_search_result,
    parse_vehicle_control_list_entry,
)
from custom_components.hikvision_next.sdk.utils import SDKError


def test_decode_sdk_license_plate_from_bytes():
    assert decode_sdk_license_plate(b"ABC1234\x00\x00") == "ABC1234"
    assert decode_sdk_license_plate(b"") == ""


def test_decode_sdk_c_string_from_char_array():
    assert decode_sdk_c_string(b"sync-op-1\x00") == "sync-op-1"
    assert decode_sdk_c_string("plain") == "plain"


def test_build_vehicle_control_list_dsalarm_event():
    class FakeAlarm:
        dwDataIndex = 42
        sOperateIndex = b"op-abc\x00"

    event = build_vehicle_control_list_dsalarm_event(FakeAlarm())
    assert event["event_id"] == "vehicle_control_list_sync"
    assert event["data_index"] == 42
    assert event["operate_index"] == "op-abc"


def test_build_vehicle_control_anpr_event():
    class FakeAlarm:
        sLicense = b"XYZ9876\x00"
        dwChannel = 43
        byPlateType = 1
        byPlateColor = 2
        byListType = 1

    event = build_vehicle_control_anpr_event(FakeAlarm())
    assert event["eventType"] == "ANPR"
    assert event["channelID"] == 43
    assert event["ANPR"]["licensePlate"] == "XYZ9876"
    assert event["ANPR"]["listType"] == 1


def test_build_gate_alarm_anpr_event_illegal_entry():
    class FakeVehicle:
        sLicense = b"GATE001\x00"
        byVehicleType = 2

    class FakeUnion:
        struVehicleInfo = FakeVehicle()

    class FakeAlarm:
        byAlarmType = GATE_ALARM_TYPE_ILLEGAL_ENTRY
        byExternalDevType = 1
        byExternalDevStatus = 0
        byExternalDevCtrlType = 0
        uAlarmInfo = FakeUnion()

    event = build_gate_alarm_anpr_event(FakeAlarm())
    assert event is not None
    assert event["eventType"] == "ANPR"
    assert event["channelID"] == 0
    assert event["ANPR"]["licensePlate"] == "GATE001"
    assert event["ANPR"]["gateAlarmType"] == GATE_ALARM_TYPE_ILLEGAL_ENTRY


def test_build_gate_alarm_anpr_event_ignores_non_vehicle_types():
    class FakeAlarm:
        byAlarmType = 0x02
        byExternalDevType = 1
        uAlarmInfo = object()

    assert build_gate_alarm_anpr_event(FakeAlarm()) is None


def test_parse_gate_alarm_anpr_notification():
    alert = ISAPIClient.parse_event_notification(
        {
            "eventType": "ANPR",
            "channelID": 0,
            "ANPR": {
                "licensePlate": "GATE001",
                "gateAlarmType": GATE_ALARM_TYPE_ILLEGAL_ENTRY,
            },
        }
    )
    assert alert is not None
    assert alert.event_id == "anpr"
    assert alert.channel_id == 0
    assert alert.anpr_license_plate == "GATE001"


def test_build_vehicle_control_cond_uses_data_index():
    cond = build_vehicle_control_cond(99)
    assert cond.dwDataIndex == 99
    assert cond.dwChannel == 0xFFFFFFFF
    assert cond.byListType & 0xFF == 0xFF


def test_parse_vehicle_control_list_entry():
    class FakeTime:
        wYear = 2026
        byMonth = 6
        byDay = 29
        byHour = 10
        byMinute = 30
        bySecond = 0

    class FakeInfo:
        dwChannel = 43
        dwDataIndex = 7
        sLicense = b"PLATE01\x00"
        byListType = 1
        byPlateType = 2
        byPlateColor = 3
        sCardNo = b"CARD-9\x00"
        sOperateIndex = b"op-7\x00"
        struStartTime = FakeTime()
        struStopTime = FakeTime()

    entry = parse_vehicle_control_list_entry(FakeInfo())
    assert entry["channel"] == 43
    assert entry["data_index"] == 7
    assert entry["license_plate"] == "PLATE01"
    assert entry["list_type"] == 1
    assert entry["list_type_name"] == LIST_TYPE_NAMES[1]
    assert entry["card_no"] == "CARD-9"
    assert entry["operate_index"] == "op-7"
    assert entry["start_time"] == "2026-06-29 10:30:00"


def test_build_lp_list_audit_search_xml():
    xml = build_lp_list_audit_search_xml(10, 25, search_id="test")
    assert "<searchResultPosition>10</searchResultPosition>" in xml
    assert "<maxResults>25</maxResults>" in xml
    assert "<searchID>test</searchID>" in xml


def test_parse_lp_list_audit_search_result():
    result = {
        "LPListAuditSearchResult": {
            "LicensePlateInfoList": {
                "numOfMatches": "1",
                "totalMatches": "2",
                "LicensePlateInfo": {
                    "id": "ABC123",
                    "LicensePlate": "ABC123",
                    "type": "whiteList",
                    "plateCategory": "stdCivilAndMilitay",
                    "plateColor": "blue",
                    "effectiveTime": "2026-01-01T00:00:00+08:00",
                },
            }
        }
    }
    entries, num, total = parse_lp_list_audit_search_result(result)
    assert num == 1
    assert total == 2
    assert len(entries) == 1
    assert entries[0]["license_plate"] == "ABC123"
    assert entries[0]["list_type_name"] == "allow"
    assert entries[0]["source"] == "isapi"


def test_is_sdk_vehicle_list_unsupported():
    unsupported = SDKError.__new__(SDKError)
    unsupported.args = ("x", 9, "recv")
    other = SDKError.__new__(SDKError)
    other.args = ("x", 5, "other")
    assert is_sdk_vehicle_list_unsupported(unsupported)
    assert not is_sdk_vehicle_list_unsupported(other)


def test_parse_vehicle_control_anpr_notification():
    alert = ISAPIClient.parse_event_notification(
        {
            "eventType": "ANPR",
            "channelID": 43,
            "ANPR": {
                "licensePlate": "XYZ9876",
                "plateType": 1,
                "plateColor": 2,
                "listType": 1,
            },
        }
    )
    assert alert is not None
    assert alert.event_id == "anpr"
    assert alert.channel_id == 43
    assert alert.anpr_license_plate == "XYZ9876"