"""Tests for notification device resolution."""

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.hikvision_next.const import DOMAIN
from custom_components.hikvision_next.isapi import AlertInfo
from custom_components.hikvision_next.notifications import EventNotificationsView


class FakeDeviceInfo:
    def __init__(self, serial_no: str, mac_address: str, ip_address: str) -> None:
        self.serial_no = serial_no
        self.mac_address = mac_address
        self.ip_address = ip_address


class FakeRuntimeDevice:
    def __init__(self, serial_no: str, mac_address: str, ip_address: str, host: str) -> None:
        self.device_info = FakeDeviceInfo(serial_no, mac_address, ip_address)
        self.host = host


def _make_entry(runtime_device: FakeRuntimeDevice | None, *, disabled: bool = False):
    entry = MagicMock()
    entry.disabled_by = "user" if disabled else None
    if runtime_device is not None:
        entry.runtime_data = runtime_device
    return entry


async def test_get_isapi_device_skips_entries_without_runtime_data(hass: HomeAssistant) -> None:
    runtime = FakeRuntimeDevice(
        "DS-K1T6QT",
        "aa:bb:cc:dd:ee:ff",
        "192.168.19.52",
        "http://192.168.19.52",
    )
    loading_entry = _make_entry(None)
    del loading_entry.runtime_data
    loaded_entry = _make_entry(runtime)

    hass.config_entries.async_entries = MagicMock(return_value=[loading_entry, loaded_entry])

    view = EventNotificationsView(hass)
    device = view.get_isapi_device(
        "192.168.19.52",
        AlertInfo(0, 0, "face", "DS-K1T6QT", "aa:bb:cc:dd:ee:ff"),
    )

    assert device is runtime


async def test_handle_subscribed_event_uses_explicit_device(hass: HomeAssistant) -> None:
    runtime = FakeRuntimeDevice(
        "DS-K1T6QT",
        "aa:bb:cc:dd:ee:ff",
        "192.168.19.52",
        "http://192.168.19.52",
    )
    runtime.cameras = []
    runtime.device_info.is_nvr = False
    runtime.get_camera_by_id = MagicMock(return_value=None)

    view = EventNotificationsView(hass)
    view.trigger_sensor = MagicMock()

    await view.handle_subscribed_event(
        {
            "eventType": "AccessControllerEvent",
            "serial": "DS-K1T6QT",
            "macAddress": "aa:bb:cc:dd:ee:ff",
            "ipAddress": "192.168.19.52",
            "AccessControllerEvent": {
                "majorEventType": 5,
                "subEventType": 0x4B,
                "employeeNoString": "1001",
                "name": "Zhang San",
            },
        },
        device=runtime,
    )

    assert view.device is runtime
    view.trigger_sensor.assert_called_once()