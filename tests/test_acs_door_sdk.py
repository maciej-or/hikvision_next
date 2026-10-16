"""Tests for SDK ACS door lock control."""

from ctypes import POINTER, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.hikvision_next.const import CONF_CONNECTION_SDK
from custom_components.hikvision_next.door_control import DoorControlAction
from custom_components.hikvision_next.hikvision_device import HikvisionDevice
from custom_components.hikvision_next.isapi import EventInfo
from custom_components.hikvision_next.isapi.isapi import ISAPIClient
from custom_components.hikvision_next.sdk.acs_door import (
    door_index_from_event_url,
    sdk_remote_control_door,
)
from custom_components.hikvision_next.sdk.hcnetsdk import (
    GatewayCommand,
    NET_DVR_CONTROL_GATEWAY,
    NET_DVR_REMOTECONTROL_GATEWAY,
)


def test_door_index_from_event_url():
    assert door_index_from_event_url("AccessControl/RemoteControl/door/1") == 1
    assert door_index_from_event_url("AccessControl/RemoteControl/door/3") == 3
    assert door_index_from_event_url(None) == 1


def test_sdk_remote_control_door_sends_close_for_lock():
    captured: list = []

    def fake_remote_control(user_id, command, buffer, size):
        captured.append((user_id, command, buffer, size))
        return True

    sdk = MagicMock()
    sdk.NET_DVR_RemoteControl.side_effect = fake_remote_control

    sdk_remote_control_door(sdk, 42, 1, DoorControlAction.CLOSE)

    user_id, command, _, _ = captured[0]
    assert user_id == 42
    assert command == NET_DVR_REMOTECONTROL_GATEWAY
    gateway = cast(captured[0][2], POINTER(NET_DVR_CONTROL_GATEWAY)).contents
    assert gateway.dwGatewayIndex == 1
    assert gateway.byCommand == GatewayCommand.CLOSE


def test_sdk_remote_control_door_sends_always_open():
    captured: list = []

    def fake_remote_control(user_id, command, buffer, size):
        captured.append((user_id, command, buffer, size))
        return True

    sdk = MagicMock()
    sdk.NET_DVR_RemoteControl.side_effect = fake_remote_control

    sdk_remote_control_door(sdk, 42, 2, DoorControlAction.ALWAYS_OPEN)

    gateway = cast(captured[0][2], POINTER(NET_DVR_CONTROL_GATEWAY)).contents
    assert gateway.dwGatewayIndex == 2
    assert gateway.byCommand == GatewayCommand.NORMALLY_OPEN


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


async def test_remote_control_door_prefers_sdk(hass: HomeAssistant) -> None:
    device = _sdk_device(hass)
    device.connection_type = CONF_CONNECTION_SDK
    device.sdk_user = 42
    device.sdk_subscription = MagicMock()
    device.device_info.serial_no = "DS-K1T6QT"

    event = EventInfo(
        id="lock",
        channel_id=0,
        io_port_id=0,
        url="AccessControl/RemoteControl/door/1",
    )

    with patch.object(
        device.hass,
        "async_add_executor_job",
        new=AsyncMock(return_value=None),
    ) as mock_exec:
        await device.remote_control_door(event, DoorControlAction.OPEN)

    mock_exec.assert_awaited_once()
    assert mock_exec.await_args.args[0] is sdk_remote_control_door


async def test_remote_control_door_falls_back_to_isapi(hass: HomeAssistant) -> None:
    device = _sdk_device(hass)
    device.connection_type = CONF_CONNECTION_SDK
    device.sdk_user = None

    event = EventInfo(
        id="lock",
        channel_id=0,
        io_port_id=0,
        url="AccessControl/RemoteControl/door/1",
    )

    with patch.object(
        ISAPIClient,
        "remote_control_door",
        new=AsyncMock(),
    ) as isapi_mock:
        await device.remote_control_door(event, DoorControlAction.OPEN)
        isapi_mock.assert_awaited_once_with(event, DoorControlAction.OPEN)