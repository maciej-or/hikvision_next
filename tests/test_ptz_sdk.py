"""Tests for SDK-native PTZ control."""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.hikvision_next.const import CONF_CONNECTION_SDK
from custom_components.hikvision_next.hikvision_device import HikvisionDevice
from custom_components.hikvision_next.isapi import IPCamera
from custom_components.hikvision_next.sdk.hcnetsdk import PAN_RIGHT, TILT_UP


@pytest.fixture
def sdk_device(hass):
    device = HikvisionDevice(
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

    with patch("custom_components.hikvision_next.hikvision_device.sdk_ptz_control_other") as mock_ptz:
        await sdk_device.ptz_start(camera, action="ptz_up")

    mock_ptz.assert_called_once_with(sdk_device.sdk_subscription, 42, 34, TILT_UP, False, speed=4)
    assert sdk_device._ptz_active[34] == {"channel": 34, "command": TILT_UP, "speed": 4}


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

    with patch("custom_components.hikvision_next.hikvision_device.sdk_ptz_control_other") as mock_ptz:
        await sdk_device.ptz_stop(camera, action="ptz_up")

    mock_ptz.assert_called_once_with(sdk_device.sdk_subscription, 42, 34, TILT_UP, True, speed=4)
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

    with (
        patch.object(sdk_device, "ptz_continuous", autospec=True) as mock_isapi,
        patch("custom_components.hikvision_next.hikvision_device.sdk_ptz_control_other") as mock_sdk,
    ):
        await sdk_device.ptz_start(camera, action="ptz_up")

    mock_isapi.assert_awaited_once()
    mock_sdk.assert_not_called()
    mock_isapi.assert_awaited_once_with(camera, pan=0, tilt=60, zoom=0)


@pytest.mark.parametrize("digital_start,expected_channel", [(1, 34), (33, 66)])
async def test_nvr_known_digital_mapping_uses_correct_native_channel(hass, sdk_device, digital_start, expected_channel):
    from types import SimpleNamespace

    sdk_device.device_info.is_nvr = True
    sdk_device.sdk_device_info = SimpleNamespace(byStartDChan=digital_start)
    camera = IPCamera(
        id=34, name="PTZ", model="test", serial_no="test", input_port=1, connection_type="Proxied", support_ptz=True
    )
    with patch("custom_components.hikvision_next.hikvision_device.sdk_ptz_control_other") as native:
        await sdk_device.ptz_start(camera, pan=40)
        native.assert_called_once_with(sdk_device.sdk_subscription, 42, expected_channel, PAN_RIGHT, False, speed=3)
        assert sdk_device._ptz_active[34] == {"channel": expected_channel, "command": PAN_RIGHT, "speed": 3}
        # Stop must follow the saved native route, even if metadata is lost.
        sdk_device.sdk_device_info = None
        await sdk_device.ptz_stop(camera)
        assert native.call_args.args == (sdk_device.sdk_subscription, 42, expected_channel, PAN_RIGHT, True)
        assert native.call_args.kwargs == {"speed": 3}


async def test_native_failure_falls_back_and_stops_via_same_isapi_route(hass, sdk_device):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from custom_components.hikvision_next.isapi.isapi import ISAPIClient
    from custom_components.hikvision_next.sdk.utils import SDKError

    sdk_device.device_info.is_nvr = True
    sdk_device.sdk_device_info = SimpleNamespace(byStartDChan=33)
    sdk_device.sdk_subscription.NET_DVR_GetLastError.return_value = 23
    sdk_device.sdk_subscription.NET_DVR_GetErrorMsg.return_value = b"unsupported"
    failure = SDKError(sdk_device.sdk_subscription, "native unsupported")
    camera = IPCamera(
        id=34, name="PTZ", model="test", serial_no="test", input_port=1, connection_type="Proxied", support_ptz=True
    )
    with (
        patch("custom_components.hikvision_next.hikvision_device.sdk_ptz_control_other", side_effect=failure) as native,
        patch.object(sdk_device, "ptz_continuous", new_callable=AsyncMock) as proxy,
        patch.object(ISAPIClient, "ptz_stop", new_callable=AsyncMock) as stop,
    ):
        await sdk_device.ptz_start(camera, tilt=40)
        native.assert_called_once()  # Never guess another camera number.
        assert native.call_args.args[2] == 66
        proxy.assert_awaited_once_with(camera, pan=0, tilt=40, zoom=0)
        await sdk_device.ptz_stop(camera)
        stop.assert_awaited_once_with(camera)
        assert 34 not in sdk_device._ptz_active
