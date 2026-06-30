"""Tests for specific ISAPI responses."""

import respx
import httpx
from contextlib import suppress
from custom_components.hikvision_next.isapi import ISAPIClient, StorageInfo
from custom_components.hikvision_next.isapi.utils import channel_from_bitmap
from tests.conftest import mock_endpoint, load_fixture


def test_channel_from_bitmap():
    assert channel_from_bitmap([1, 0, 0]) == 1
    assert channel_from_bitmap([0, 1, 0]) == 2
    assert channel_from_bitmap([0, 0, 0]) == 0


def test_parse_sdk_motion_detection_channel_bitmap():
    alert = ISAPIClient.parse_event_notification(
        {
            "eventType": "MotionDetection",
            "channels": [1] + [0] * 63,
        }
    )
    assert alert is not None
    assert alert.event_id == "motiondetection"
    assert alert.channel_id == 1


def test_parse_event_type_options_supports_opt_attribute():
    options = ISAPIClient._parse_event_type_options(
        {"opt": "motionDetection,VMD,fieldDetection,fielddetection"}
    )
    assert "motionDetection" in options
    assert "VMD" in options


def test_event_trigger_request_ids_prioritize_vmd():
    client = ISAPIClient("http://1.0.0.1", "u", "p")
    ids = client._event_trigger_request_ids("motiondetection", 5)
    assert ids[0] == "VMD-5"
    assert "motiondetection-5" in ids
    assert "vmd-5" in ids
    assert "motionDetection-5" in ids


def test_normalize_event_id_maps_vmd_aliases():
    assert ISAPIClient._normalize_event_id("VMD") == "motiondetection"
    assert ISAPIClient._normalize_event_id("motionDetection") == "motiondetection"
    assert ISAPIClient._normalize_event_id("thermometry") == "motiondetection"
    assert ISAPIClient._normalize_event_id("unknownEvent") is None


def test_normalize_event_id_maps_face_aliases():
    assert ISAPIClient._normalize_event_id("faceSnap") == "facesnap"
    assert ISAPIClient._normalize_event_id("faceCapture") == "facesnap"
    assert ISAPIClient._normalize_event_id("faceContrast") == "facecontrast"
    assert ISAPIClient._normalize_event_id("faceDetection") == "facedetection"


def test_parse_face_snap_event_notification_is_ignored():
    """Face snap events only drive the image entity, not binary sensors."""
    assert ISAPIClient.parse_event_notification({"eventType": "faceSnap", "channelID": 2}) is None


def test_parse_acs_lock_event_major_event():
    alert = ISAPIClient.parse_event_notification(
        {
            "eventType": "AccessControllerEvent",
            "AccessControllerEvent": {"majorEventType": 5, "subEventType": 0x15},
        }
    )
    assert alert is not None
    assert alert.event_id == "lock"
    assert alert.channel_id == 0
    assert alert.state == "on"


def test_parse_acs_face_verify_pass_event():
    alert = ISAPIClient.parse_event_notification(
        {
            "eventType": "AccessControllerEvent",
            "AccessControllerEvent": {
                "majorEventType": 5,
                "subEventType": 0x4B,
                "employeeNoString": "1001",
                "name": "Zhang San",
            },
        }
    )
    assert alert is not None
    assert alert.event_id == "face"
    assert alert.state == "Zhang San"
    assert alert.face_person_name == "Zhang San"
    assert alert.face_employee_no == "1001"


def test_parse_acs_remote_open_door_operation():
    alert = ISAPIClient.parse_event_notification(
        {
            "eventType": "AccessControllerEvent",
            "AccessControllerEvent": {"majorEventType": 3, "subEventType": 0x400},
        }
    )
    assert alert is not None
    assert alert.event_id == "lock"
    assert alert.channel_id == 0
    assert alert.state == "on"


def test_parse_vmd_event_notification():
    alert = ISAPIClient.parse_event_notification({"eventType": "VMD", "channelID": 5})
    assert alert is not None
    assert alert.event_id == "motiondetection"
    assert alert.channel_id == 5


@respx.mock
async def test_storage(mock_isapi):
    isapi = mock_isapi

    mock_endpoint("ContentMgmt/Storage", "hdd1")
    storage_list = await isapi.get_storage_devices()
    assert len(storage_list) == 1
    assert storage_list[0] == StorageInfo(
        id=1,
        name="hdd1",
        type="SATA",
        status="ok",
        capacity=1907729,
        freespace=0,
        property="RW",
        ip="",
    )

    mock_endpoint("ContentMgmt/Storage", "hdd1_nas1")
    storage_list = await isapi.get_storage_devices()
    assert len(storage_list) == 2
    assert storage_list[0].type == "SATA"
    assert storage_list[1].type == "NFS"
    assert storage_list[1].ip != ""

    mock_endpoint("ContentMgmt/Storage", status_code=500)
    with suppress(Exception):
        storage_list = await isapi.get_storage_devices()
        assert len(storage_list) == 0


@respx.mock
async def test_notification_hosts(mock_isapi):
    isapi = mock_isapi

    mock_endpoint("Event/notification/httpHosts", "nvr_single_item")
    host_nvr = await isapi.get_alarm_server()

    mock_endpoint("Event/notification/httpHosts", "ipc_list")
    host_ipc = await isapi.get_alarm_server()

    assert host_nvr == host_ipc


@respx.mock
async def test_update_notification_hosts(mock_isapi):
    isapi = mock_isapi

    def update_side_effect(request, route):
        payload = load_fixture("ISAPI/Event.notification.httpHosts", "set_alarm_server_payload")
        if request.content.decode("utf-8") != payload:
            raise AssertionError("Request content does not match expected payload")
        return httpx.Response(200)

    mock_endpoint("Event/notification/httpHosts", "nvr_single_item")
    url = f"{isapi.host}/ISAPI/Event/notification/httpHosts"
    endpoint = respx.put(url).mock(side_effect=update_side_effect)
    await isapi.set_alarm_server("http://1.0.0.11:8123", "/api/hikvision")

    assert endpoint.called


@respx.mock
async def test_update_notification_hosts_from_ipaddress_to_hostname(mock_isapi):
    isapi = mock_isapi

    def update_side_effect(request, route):
        payload = load_fixture("ISAPI/Event.notification.httpHosts", "set_alarm_server_outside_network_payload")
        if request.content.decode("utf-8") != payload:
            raise AssertionError("Request content does not match expected payload")
        return httpx.Response(200)

    mock_endpoint("Event/notification/httpHosts", "nvr_single_item")
    url = f"{isapi.host}/ISAPI/Event/notification/httpHosts"
    endpoint = respx.put(url).mock(side_effect=update_side_effect)
    await isapi.set_alarm_server("https://ha.hostname.domain", "/api/hikvision")

    assert endpoint.called
