"""Tests for ANPR snap image entities and SDK image extraction."""

from ctypes import POINTER, addressof, cast, create_string_buffer, sizeof
from dataclasses import replace

from custom_components.hikvision_next.const import ANPR_IMAGE_SUFFIX, ANPR_LICENSE_PLATE_SENSOR_SUFFIX
from custom_components.hikvision_next.sdk.hcnetsdk import NET_DVR_PLATE_RESULT
from custom_components.hikvision_next.sdk.utils import (
    read_anpr_image_from_plate_result,
    read_anpr_image_from_vehicle_control,
)
from tests.test_anpr_entities import FakeHikvisionDevice


def test_anpr_image_unique_id_for_standalone_its():
    device = FakeHikvisionDevice()
    events = device.get_device_event_capabilities()
    anpr = next(event for event in events if event.id == "anpr")
    assert anpr.anpr_image_unique_id.endswith(f"_{ANPR_IMAGE_SUFFIX}")
    assert anpr.anpr_plate_unique_id.endswith(f"_{ANPR_LICENSE_PLATE_SENSOR_SUFFIX}")


def test_anpr_image_unique_id_for_nvr_channel():
    from custom_components.hikvision_next.isapi import EventInfo

    device = FakeHikvisionDevice()
    device.device_info = replace(device.device_info, is_nvr=True)
    device.supported_events = [
        EventInfo(id="anpr", channel_id=43, io_port_id=0, notifications=["center"]),
    ]
    events = device.get_device_event_capabilities(camera_id=43)
    anpr = events[0]
    assert anpr.anpr_image_unique_id.endswith("_43_anpr_snap")


def test_read_anpr_image_from_plate_result_prefers_scene_image():
    struct_size = sizeof(NET_DVR_PLATE_RESULT)
    scene = b"\xff\xd8\xff\xe0scene"
    plate = b"\xff\xd8\xff\xe0plate"
    buf = create_string_buffer(struct_size + len(scene) + len(plate))
    plate_ptr = cast(buf, POINTER(NET_DVR_PLATE_RESULT))
    alarm = plate_ptr.contents
    alarm.dwPicLen = len(scene)
    alarm.dwPicPlateLen = len(plate)
    payload_base = addressof(buf) + struct_size
    from ctypes import c_void_p

    alarm.pBuffer1 = cast(payload_base, c_void_p)
    alarm.pBuffer2 = cast(payload_base + len(scene), c_void_p)
    memmove = __import__("ctypes").memmove
    memmove(payload_base, scene, len(scene))
    memmove(payload_base + len(scene), plate, len(plate))

    image = read_anpr_image_from_plate_result(alarm)
    assert image == scene


def test_read_anpr_image_from_plate_result_falls_back_to_plate_crop():
    struct_size = sizeof(NET_DVR_PLATE_RESULT)
    plate = b"\xff\xd8\xff\xe0plate"
    buf = create_string_buffer(struct_size + len(plate))
    plate_ptr = cast(buf, POINTER(NET_DVR_PLATE_RESULT))
    alarm = plate_ptr.contents
    alarm.dwPicPlateLen = len(plate)
    payload_base = addressof(buf) + struct_size
    from ctypes import c_void_p

    alarm.pBuffer2 = cast(payload_base, c_void_p)
    __import__("ctypes").memmove(payload_base, plate, len(plate))

    image = read_anpr_image_from_plate_result(alarm)
    assert image == plate


def test_read_anpr_image_from_vehicle_control_skips_url_mode():
    class FakeAlarm:
        pPicData = 0x1234
        dwPicDataLen = 1000
        byPicTransType = 1

    assert read_anpr_image_from_vehicle_control(FakeAlarm()) is None