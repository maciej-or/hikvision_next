"""Tests for ANPR entity discovery and plate text updates."""

from dataclasses import dataclass, field, replace

from custom_components.hikvision_next.const import (
    ANPR_IMAGE_SUFFIX,
    ANPR_LICENSE_PLATE_SENSOR_SUFFIX,
    DEVICE_LEVEL_EVENT_IDS,
)
from custom_components.hikvision_next.isapi import EventInfo, ISAPIClient
from custom_components.hikvision_next.isapi.const import EVENT_IO, EVENT_TRAFFIC
from custom_components.hikvision_next.sdk.utils import (
    build_its_plate_anpr_event,
    build_upload_plate_anpr_event,
)


@dataclass
class FakeDeviceInfo:
    serial_no: str = "iDS-2CD9545-ESU 20220311AIJ64043115"
    is_nvr: bool = False


@dataclass
class FakeCapabilities:
    support_video_intercom: bool = False


class FakeHikvisionDevice:
    device_info = FakeDeviceInfo()
    capabilities = FakeCapabilities()
    supported_events = [
        EventInfo(id="anpr", channel_id=0, io_port_id=0, notifications=["center"]),
        EventInfo(id="io", channel_id=0, io_port_id=1, notifications=["center"]),
    ]

    def _event_matches_capabilities_scope(self, event: EventInfo, camera_id: int | None) -> bool:
        from custom_components.hikvision_next.const import EVENTS

        if event.id not in EVENTS:
            return False
        if camera_id is None:
            if self.device_info.is_nvr and event.id == "anpr":
                return False
            event_type = EVENTS[event.id].get("type")
            return event_type == EVENT_IO or event.id in DEVICE_LEVEL_EVENT_IDS
        if event.channel_id == int(camera_id):
            return True
        if event.id == "anpr":
            return False
        return (
            self.device_info.is_nvr
            and event.channel_id == 0
            and event.id in DEVICE_LEVEL_EVENT_IDS
        )

    def get_device_event_capabilities(self, camera_id: int | None = None):
        from homeassistant.util import slugify

        events = []
        integration_supported_events = [
            event
            for event in self.supported_events
            if self._event_matches_capabilities_scope(event, camera_id)
        ]
        for event in integration_supported_events:
            device_id_param = f"_{camera_id}" if camera_id else ""
            io_port_id_param = f"_{event.io_port_id}" if event.io_port_id != 0 else ""
            serial = slugify(self.device_info.serial_no.lower())
            unique_id = f"{serial}{device_id_param}{io_port_id_param}_{event.id}"
            anpr_plate_unique_id = None
            anpr_image_unique_id = None
            if event.id == "anpr":
                anpr_plate_unique_id = (
                    f"{serial}{device_id_param}{io_port_id_param}_{ANPR_LICENSE_PLATE_SENSOR_SUFFIX}"
                )
                anpr_image_unique_id = (
                    f"{serial}{device_id_param}{io_port_id_param}_{ANPR_IMAGE_SUFFIX}"
                )
            events.append(
                replace(
                    event,
                    unique_id=unique_id,
                    anpr_plate_unique_id=anpr_plate_unique_id,
                    anpr_image_unique_id=anpr_image_unique_id,
                )
            )
        return events


def test_device_level_capabilities_include_anpr_for_its_camera():
    device = FakeHikvisionDevice()
    events = device.get_device_event_capabilities()
    assert any(event.id == "anpr" for event in events)
    anpr = next(event for event in events if event.id == "anpr")
    assert anpr.unique_id.endswith("_anpr")
    assert anpr.anpr_plate_unique_id.endswith(f"_{ANPR_LICENSE_PLATE_SENSOR_SUFFIX}")


def test_anpr_event_type_is_traffic_not_io():
    from custom_components.hikvision_next.isapi.const import EVENTS

    assert EVENTS["anpr"]["type"] == EVENT_TRAFFIC
    assert "anpr" in DEVICE_LEVEL_EVENT_IDS


def test_build_upload_plate_anpr_event_from_plate_result():
    class FakePlate:
        sLicense = b"B9B221\x00"
        byEntireBelieve = 90
        byColor = 1
        byPlateType = 2

    class FakeAlarm:
        struPlateInfo = FakePlate()
        byChanIndex = 1
        byDriveChan = 1
        byCarDirectionType = 1

    event = build_upload_plate_anpr_event(FakeAlarm())
    assert event is not None
    assert event["eventType"] == "ANPR"
    assert event["ANPR"]["licensePlate"] == "B9B221"


def test_build_its_plate_anpr_event_from_plate_info():
    class FakePlate:
        sLicense = b"ABC1234\x00"
        byEntireBelieve = 88
        byColor = 2
        byPlateType = 1

    class FakeAlarm:
        struPlateInfo = FakePlate()
        byChanIndex = 1
        byChanIndexEx = 0
        byDriveChan = 1

    event = build_its_plate_anpr_event(FakeAlarm())
    assert event is not None
    assert event["eventType"] == "ANPR"
    assert event["ANPR"]["licensePlate"] == "ABC1234"


def test_standalone_its_anpr_only_at_device_level_not_camera():
    device = FakeHikvisionDevice()
    device.cameras = [type("Cam", (), {"id": 1})()]
    assert not device.get_device_event_capabilities(camera_id=1)
    device_events = device.get_device_event_capabilities()
    assert any(event.id == "anpr" for event in device_events)
    assert device_events[0].unique_id.endswith("_anpr")
    assert "_1_" not in device_events[0].unique_id


def test_nvr_global_anpr_not_broadcast_to_every_channel():
    device = FakeHikvisionDevice()
    device.device_info.is_nvr = True
    device.supported_events = [
        EventInfo(id="anpr", channel_id=0, io_port_id=0, notifications=["center"]),
    ]
    assert not device.get_device_event_capabilities(camera_id=38)
    assert not device.get_device_event_capabilities()


def test_nvr_anpr_unique_ids_are_per_camera_not_shared():
    device = FakeHikvisionDevice()
    device.device_info.is_nvr = True
    device.supported_events = [
        EventInfo(id="anpr", channel_id=43, io_port_id=0, notifications=["record"]),
        EventInfo(id="anpr", channel_id=45, io_port_id=0, notifications=["center"]),
    ]
    events_43 = device.get_device_event_capabilities(camera_id=43)
    events_45 = device.get_device_event_capabilities(camera_id=45)
    anpr_43 = next(event for event in events_43 if event.id == "anpr")
    anpr_45 = next(event for event in events_45 if event.id == "anpr")
    assert anpr_43 is not anpr_45
    assert anpr_43.unique_id.endswith("_43_anpr")
    assert anpr_45.unique_id.endswith("_45_anpr")
    assert anpr_43.anpr_plate_unique_id.endswith("_43_anpr_plate")
    assert anpr_45.anpr_plate_unique_id.endswith("_45_anpr_plate")


def test_nvr_camera_scoped_anpr_entity_ids():
    device = FakeHikvisionDevice()
    device.device_info.is_nvr = True
    device.supported_events = [
        EventInfo(id="anpr", channel_id=43, io_port_id=0, notifications=["record"]),
    ]
    events = device.get_device_event_capabilities(camera_id=43)
    assert len(events) == 1
    anpr = events[0]
    assert anpr.unique_id.endswith("_43_anpr")
    assert anpr.anpr_plate_unique_id.endswith("_43_anpr_plate")


def test_parse_anpr_notification_extracts_license_plate():
    alert = ISAPIClient.parse_event_notification(
        {
            "eventType": "ANPR",
            "channelID": 1,
            "ANPR": {"licensePlate": "沪B9B221", "confidenceLevel": 95},
        }
    )
    assert alert is not None
    assert alert.event_id == "anpr"
    assert alert.anpr_license_plate == "沪B9B221"
    assert alert.anpr_confidence_level == 95