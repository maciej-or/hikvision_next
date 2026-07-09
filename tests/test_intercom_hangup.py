"""Tests for intercom hangup call release."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.hikvision_next.const import CONF_CONNECTION_SDK
from custom_components.hikvision_next.hikvision_device import HikvisionDevice
from custom_components.hikvision_next.sdk.hcnetsdk import VideoCallCmdType


def _sdk_device(hass: HomeAssistant) -> HikvisionDevice:
    return HikvisionDevice(
        hass,
        data={
            "host": "http://192.168.28.2",
            "username": "admin",
            "password": "pass",
            "connection_type": CONF_CONNECTION_SDK,
            "set_alarm_server": False,
            "alarm_server": "",
            "verify_ssl": True,
        },
    )


@pytest.fixture
def intercom_device(hass: HomeAssistant) -> HikvisionDevice:
    device = _sdk_device(hass)
    device.connection_type = CONF_CONNECTION_SDK
    device.capabilities.support_video_intercom = True
    device.intercom_coordinator = MagicMock()
    device.sdk_user = 1
    device._video_intercom = MagicMock()
    device._send_intercom_command = AsyncMock()
    return device


async def test_intercom_hangup_sends_end_call_then_reject(
    intercom_device: HikvisionDevice,
) -> None:
    await intercom_device.intercom_hangup()

    assert intercom_device._send_intercom_command.await_count == 2
    intercom_device._send_intercom_command.assert_any_await(VideoCallCmdType.END_CALL)
    intercom_device._send_intercom_command.assert_any_await(
        VideoCallCmdType.REJECT_CALL,
        apply_state=False,
    )


async def test_reconnect_video_intercom_tears_down_and_starts(
    hass: HomeAssistant,
) -> None:
    device = _sdk_device(hass)
    device.connection_type = CONF_CONNECTION_SDK
    device.capabilities.support_video_intercom = True
    device.intercom_coordinator = MagicMock()
    device.sdk_user = 1
    device.device_info.serial_no = "DS-K1T6QT TEST"

    session = MagicMock()
    device._video_intercom = session

    with (
        patch.object(device, "_teardown_video_intercom_session", new=AsyncMock()) as teardown,
        patch.object(device, "_start_video_intercom_remote_config", new=AsyncMock()) as start,
    ):
        await device._reconnect_video_intercom_remote_config()

    teardown.assert_awaited_once()
    start.assert_awaited_once()
    assert session.stop.call_count == 0