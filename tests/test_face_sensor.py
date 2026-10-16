"""Tests for face recognition text sensor."""

import ctypes
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.hikvision_next.const import DOMAIN
from custom_components.hikvision_next.isapi import AlertInfo, EventInfo, ISAPIClient
from custom_components.hikvision_next.notifications import EventNotificationsView
from custom_components.hikvision_next.sdk.acsalarminfo import ACS_FACE_VERIFY_PASS_MINORS
from custom_components.hikvision_next.sdk.acs_event import build_acs_access_controller_event
from custom_components.hikvision_next.sdk.utils import read_acs_face_image_from_alarm
from custom_components.hikvision_next.sensor import FacePersonSensor
from custom_components.hikvision_next.image import FaceVerifyImage


def test_parse_face_verify_pass_with_name_and_employee():
    alert = ISAPIClient.parse_event_notification(
        {
            "eventType": "AccessControllerEvent",
            "AccessControllerEvent": {
                "majorEventType": 5,
                "subEventType": 0x4B,
                "employeeNoString": "1001",
                "name": "Zhang San",
                "cardNo": "12345678",
            },
        }
    )
    assert alert is not None
    assert alert.event_id == "face"
    assert alert.state == "Zhang San"
    assert alert.face_person_name == "Zhang San"
    assert alert.face_employee_no == "1001"
    assert alert.face_card_no == "12345678"


@pytest.mark.parametrize("minor", sorted(ACS_FACE_VERIFY_PASS_MINORS))
def test_parse_face_verify_pass_accepts_all_face_minors(minor: int):
    alert = ISAPIClient.parse_event_notification(
        {
            "eventType": "AccessControllerEvent",
            "AccessControllerEvent": {
                "majorEventType": 5,
                "subEventType": minor,
                "employeeNoString": "42",
            },
        }
    )
    assert alert is not None
    assert alert.event_id == "face"
    assert alert.state == "42"
    assert alert.face_employee_no == "42"


def test_parse_face_verify_pass_falls_back_to_employee_only():
    alert = ISAPIClient.parse_event_notification(
        {
            "eventType": "AccessControllerEvent",
            "AccessControllerEvent": {
                "majorEventType": 5,
                "subEventType": 0x4B,
                "employeeNoString": "1001",
            },
        }
    )
    assert alert is not None
    assert alert.state == "1001"
    assert alert.face_person_name is None


def test_read_acs_face_image_from_alarm_reads_inline_jpeg():
    jpeg = b"\xff\xd8\xff\xd9extra"
    buffer = ctypes.create_string_buffer(jpeg)

    class FakeAlarm:
        pPicData = ctypes.cast(buffer, ctypes.c_void_p)
        dwPicDataLen = len(jpeg)
        byPicTransType = 0

    assert read_acs_face_image_from_alarm(FakeAlarm()) == jpeg


def test_read_acs_face_image_from_alarm_skips_url_transport():
    class FakeAlarm:
        pPicData = 1
        dwPicDataLen = 100
        byPicTransType = 1

    assert read_acs_face_image_from_alarm(FakeAlarm()) is None


def test_build_acs_access_controller_event_uses_employee_and_card():
    class FakeAcsEvent:
        byCardNo = b"99887766\x00"
        byReportChannel = 1
        dwDoorNo = 2
        dwEmployeeNo = 1001

    class FakeAlarm:
        dwMajor = 5
        dwMinor = 0x4B
        struAcsEventInfo = FakeAcsEvent()
        byAcsEventInfoExtend = 0
        pAcsEventInfoExtend = None
        byAcsEventInfoExtendV20 = 0
        pAcsEventInfoExtendV20 = None

    event = build_acs_access_controller_event(FakeAlarm())
    ace = event["AccessControllerEvent"]
    assert ace["employeeNoString"] == "1001"
    assert ace["cardNo"] == "99887766"
    assert ace["doorNo"] == 2


async def test_face_sensor_resets_to_unknown_after_pulse(hass: HomeAssistant) -> None:
    """Face sensor pulses person name for 1s then returns to unknown."""
    import asyncio

    from custom_components.hikvision_next.const import FACE_PERSON_PULSE_SECONDS

    device = MagicMock()
    device.hass_device_info.return_value = {}

    event_info = EventInfo(
        id="face",
        channel_id=0,
        io_port_id=0,
        unique_id="ds_k1t6qt_face",
    )
    entity = FacePersonSensor(device, 0, event_info)
    entity.hass = hass
    entity.platform = MagicMock()

    entity.set_person("Zhang San", {"name": "Zhang San"})
    assert entity._attr_native_value == "Zhang San"

    await asyncio.sleep(FACE_PERSON_PULSE_SECONDS + 0.1)
    await hass.async_block_till_done()

    assert entity._attr_native_value == "unknown"
    assert entity._attr_extra_state_attributes == {}


async def test_trigger_face_sensor_updates_entity(hass: HomeAssistant) -> None:
    """Face events update the text sensor with person name and attributes."""
    device = MagicMock()
    device.device_info.serial_no = "DS-K1T6QT F72MW20250311V032740CHGD2751823"
    device.device_info.is_nvr = False
    device.cameras = []
    device.get_camera_by_id.return_value = None

    event_info = EventInfo(
        id="face",
        channel_id=0,
        io_port_id=0,
        unique_id="ds_k1t6qt_f72mw20250311v032740chgd2751823_face",
    )

    entity = FacePersonSensor(device, 0, event_info)
    entity.hass = hass
    entity.entity_id = f"sensor.{event_info.unique_id}"
    hass.data.setdefault(DOMAIN, {})["event_text_sensors"] = {entity.unique_id: entity}

    view = EventNotificationsView(hass)
    view.device = device
    _, lookup_uid = view._lookup_event_entity(
        AlertInfo(channel_id=0, io_port_id=0, event_id="face")
    )
    assert lookup_uid == entity.unique_id
    view.trigger_sensor(
        AlertInfo(
            channel_id=0,
            io_port_id=0,
            event_id="face",
            state="Zhang San",
            face_person_name="Zhang San",
            face_employee_no="1001",
            face_card_no="12345678",
        )
    )
    await hass.async_block_till_done()

    assert entity._attr_native_value == "Zhang San"
    assert entity._attr_extra_state_attributes["name"] == "Zhang San"
    assert entity._attr_extra_state_attributes["employee_no"] == "1001"
    assert entity._attr_extra_state_attributes["card_no"] == "12345678"
    entity._cancel_face_reset()


async def test_update_face_verify_image(hass: HomeAssistant) -> None:
    """ACS face verify image entity receives SDK callback bytes."""
    device = MagicMock()
    device.device_info.serial_no = "DS-K1T6QT F72MW20250311V032740CHGD2751823"
    device.hass_device_info.return_value = {}

    entity = FaceVerifyImage(hass, device)
    hass.data.setdefault(DOMAIN, {})["face_verify_images"] = {entity.unique_id: entity}

    view = EventNotificationsView(hass)
    jpeg = b"\xff\xd8\xff\xd9"
    with patch.object(entity, "schedule_update_ha_state"):
        await view.update_face_verify_image(
            device,
            jpeg,
            person_name="Zhang San",
            employee_no="1001",
            card_no="12345678",
            pic_len=41157,
        )

    assert entity.image() == jpeg
    assert entity._attr_image_last_updated is not None
    assert entity._attr_extra_state_attributes["name"] == "Zhang San"
    assert entity._attr_extra_state_attributes["employee_no"] == "1001"
    assert entity._attr_extra_state_attributes["card_no"] == "12345678"
    assert entity._attr_extra_state_attributes["pic_data_len"] == 41157