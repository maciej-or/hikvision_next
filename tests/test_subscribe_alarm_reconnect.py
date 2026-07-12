"""Tests for SDK alarm channel reconnect scheduling."""

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.hikvision_next.const import CONF_CONNECTION_SDK, DOMAIN
from custom_components.hikvision_next.coordinator import SubscribeStatusCoordinator
from custom_components.hikvision_next.hikvision_device import HikvisionDevice
from custom_components.hikvision_next.sdk.video_intercom import (
    SUBSCRIBE_ALARM_RECONNECT_BASE_DELAY,
)


class FakeDeviceInfo:
    serial_no = "DS-K1T6QT"


def _make_device(hass: HomeAssistant):
    device = HikvisionDevice.__new__(HikvisionDevice)
    device.hass = hass
    device.connection_type = CONF_CONNECTION_SDK
    device.sdk_user = 1
    device.sdk_subscription = MagicMock()
    device.device_info = FakeDeviceInfo()
    device.subscribe_coordinator = SubscribeStatusCoordinator(hass, device)
    device._subscribe_reconnect_unsub = None
    device._subscribe_reconnect_attempts = 0
    return device


def test_should_reconnect_subscribe_requires_sdk_session():
    device = HikvisionDevice.__new__(HikvisionDevice)
    device.connection_type = CONF_CONNECTION_SDK
    device.sdk_user = 1
    device.sdk_subscription = object()
    device.subscribe_coordinator = object()

    assert HikvisionDevice._should_reconnect_subscribe(device) is True

    device.sdk_user = None
    assert HikvisionDevice._should_reconnect_subscribe(device) is False


async def test_setup_sdk_alarm_channel_failure_schedules_reconnect(hass: HomeAssistant) -> None:
    device = _make_device(hass)
    device._build_sdk_alarm_param = HikvisionDevice._build_sdk_alarm_param.__get__(device, HikvisionDevice)
    device._teardown_sdk_alarm_channel = MagicMock()
    device._sdk_alarm_error_message = MagicMock(return_value="1924: Deploy exceed max")
    device.sdk_subscription.NET_DVR_SetupAlarmChan_V50.return_value = -1
    device.sdk_subscription.NET_DVR_GetLastError.return_value = 1924

    with patch(
        "custom_components.hikvision_next.hikvision_device.async_call_later"
    ) as mock_call_later:
        success = await HikvisionDevice._setup_sdk_alarm_channel(device)

    assert success is False
    assert device.subscribe_coordinator.connected is False
    assert device.subscribe_coordinator.reason == "1924: Deploy exceed max"
    assert device._subscribe_reconnect_attempts == 1
    mock_call_later.assert_called_once()
    assert mock_call_later.call_args.args[1] == SUBSCRIBE_ALARM_RECONNECT_BASE_DELAY


async def test_setup_sdk_alarm_channel_success_clears_reconnect_state(hass: HomeAssistant) -> None:
    device = _make_device(hass)
    device._subscribe_reconnect_attempts = 3
    reconnect_unsub = MagicMock()
    device._subscribe_reconnect_unsub = reconnect_unsub
    device._build_sdk_alarm_param = HikvisionDevice._build_sdk_alarm_param.__get__(device, HikvisionDevice)
    device._teardown_sdk_alarm_channel = MagicMock()
    device.sdk_subscription.NET_DVR_SetupAlarmChan_V50.return_value = 7

    success = await HikvisionDevice._setup_sdk_alarm_channel(device)

    assert success is True
    assert device.sdk_handle == 7
    assert device.subscribe_coordinator.connected is True
    assert device._subscribe_reconnect_attempts == 0
    reconnect_unsub.assert_called_once()
    assert device._subscribe_reconnect_unsub is None


async def test_async_set_subscribe_connected_stopped_does_not_schedule(hass: HomeAssistant) -> None:
    device = _make_device(hass)

    with patch(
        "custom_components.hikvision_next.hikvision_device.async_call_later"
    ) as mock_call_later:
        await device.async_set_subscribe_connected(False, "stopped")

    mock_call_later.assert_not_called()