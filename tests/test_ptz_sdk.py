"""Tests for SDK-native PTZ control."""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.hikvision_next.const import CONF_CONNECTION_SDK
from custom_components.hikvision_next.hikvision_device import HikvisionDevice
from custom_components.hikvision_next.isapi import IPCamera
from custom_components.hikvision_next.sdk.hcnetsdk import PAN_RIGHT, TILT_UP


@pytest.fixture
def sdk_device(hass):
    device = HikvisionDevice(hass, data={
        "host": "http://192.168.28.2",
        "username": "admin",
        "password": "pass",
        "connection_type": CONF_CONNECTION_SDK,
        "set_alarm_server": False,
        "alarm_server": "",
        "verify_ssl": True,
    })
    device.connection_type = CONF_CONNECTION_SDK
    device.sdk_subscription = MagicMock()
    device.sdk_user = 42
    return device


def test_ptz_command_from_velocities():
    from custom_components.hikvision_next.isapi.utils import (
        ptz_command_from_action,
        ptz_command_from_velocities,
    )
    from custom_components.hikvision_next.sdk.hcnetsdk import ZOOM_OUT

    assert ptz_command_from_action("ptz_up") == TILT_UP
    assert ptz_command_from_velocities(60, 0, 0) == PAN_RIGHT
    assert ptz_command_from_velocities(0, 0, -60) == ZOOM_OUT


async def test_sdk_ptz_start_uses_native_api(hass, sdk_device):
    camera = IPCamera(
        id=34,
        name="PTZ Cam",
        model="test",
        serial_no="serial",
        input_port=34,
        connection_type="Proxied",
        streams=[],
        support_ptz=True,
    )

    async def run_executor(func, *args):
        func(*args)

    hass.async_add_executor_job = run_executor

    with patch(
        "custom_components.hikvision_next.hikvision_device.sdk_ptz_control_other"
    ) as mock_ptz:
        await sdk_device.ptz_start(camera, action="ptz_up")

    mock_ptz.assert_called_once_with(
        sdk_device.sdk_subscription, 42, 34, TILT_UP, False, speed=4
    )
    assert sdk_device._ptz_active[34] == {"channel": 34, "command": TILT_UP}


async def test_sdk_ptz_stop_uses_same_command(hass, sdk_device):
    camera = IPCamera(
        id=34,
        name="PTZ Cam",
        model="test",
        serial_no="serial",
        input_port=34,
        connection_type="Proxied",
        streams=[],
        support_ptz=True,
    )
    sdk_device._ptz_active[34] = {"channel": 34, "command": TILT_UP}

    async def run_executor(func, *args):
        func(*args)

    hass.async_add_executor_job = run_executor

    with patch(
        "custom_components.hikvision_next.hikvision_device.sdk_ptz_control_other"
    ) as mock_ptz:
        await sdk_device.ptz_stop(camera, action="ptz_up")

    mock_ptz.assert_called_once_with(
        sdk_device.sdk_subscription, 42, 34, TILT_UP, True, speed=4
    )
    assert 34 not in sdk_device._ptz_active


async def test_nvr_proxied_ptz_uses_isapi_not_sdk(hass, sdk_device):
    """NVR proxied IP cameras must use ISAPI PTZCtrlProxy, not SDK PTZ APIs."""
    sdk_device.device_info.is_nvr = True
    camera = IPCamera(
        id=34,
        name="PTZ Cam",
        model="test",
        serial_no="serial",
        input_port=2,
        connection_type="Proxied",
        streams=[],
        support_ptz=True,
    )

    with patch.object(sdk_device, "ptz_continuous", autospec=True) as mock_isapi:
        with patch(
            "custom_components.hikvision_next.hikvision_device.sdk_ptz_control_other"
        ) as mock_sdk:
            await sdk_device.ptz_start(camera, action="ptz_up")

    mock_isapi.assert_awaited_once()
    mock_sdk.assert_not_called()