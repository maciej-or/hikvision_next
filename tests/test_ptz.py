"""Tests for PTZ detection and control."""

import pytest
import respx
import xmltodict
from custom_components.hikvision_next.isapi import IPCamera
from custom_components.hikvision_next.isapi.isapi import ISAPIClient
from tests.conftest import TEST_HOST, mock_device_endpoints


@pytest.mark.parametrize("init_integration", ["DS-2SE4C425MWG-E-26"], indirect=True)
async def test_ptz_support_detected_for_supported_channel(init_integration) -> None:
    """PTZ support is detected only on channels that expose PTZ capabilities."""
    device = init_integration.runtime_data
    ptz_cameras = [camera for camera in device.cameras if camera.support_ptz]
    assert len(ptz_cameras) == 1
    assert ptz_cameras[0].id == 1
    assert not device.cameras[1].support_ptz


@respx.mock
async def test_ptz_continuous_sends_put_request() -> None:
    """ptz_continuous issues the expected ISAPI PUT payload."""
    mock_device_endpoints("DS-2SE4C425MWG-E-26")
    continuous_url = f"{TEST_HOST}/ISAPI/PTZCtrl/channels/1/continuous"
    route = respx.route(method="PUT", url=continuous_url).respond(text="<ResponseStatus>ok</ResponseStatus>")

    client = ISAPIClient(TEST_HOST, "u1", "p1")
    await client.get_hardware_info()
    camera = next(cam for cam in client.cameras if cam.id == 1)
    assert camera.support_ptz

    await client.ptz_continuous(camera, pan=60, tilt=0, zoom=0)

    assert route.called
    payload = xmltodict.parse(route.calls.last.request.content)
    assert payload["PTZData"]["pan"] == "60"
    assert payload["PTZData"]["tilt"] == "0"
    assert payload["PTZData"]["zoom"] == "0"


@respx.mock
async def test_ptz_stop_sends_zero_speed() -> None:
    """ptz_stop sends zero velocities to the continuous endpoint."""
    mock_device_endpoints("DS-2SE4C425MWG-E-26")
    continuous_url = f"{TEST_HOST}/ISAPI/PTZCtrl/channels/1/continuous"
    route = respx.route(method="PUT", url=continuous_url).respond(text="<ResponseStatus>ok</ResponseStatus>")

    client = ISAPIClient(TEST_HOST, "u1", "p1")
    await client.get_hardware_info()
    camera = next(cam for cam in client.cameras if cam.id == 1)

    await client.ptz_stop(camera)

    assert route.called
    payload = xmltodict.parse(route.calls.last.request.content)
    assert payload["PTZData"]["pan"] == "0"
    assert payload["PTZData"]["tilt"] == "0"
    assert payload["PTZData"]["zoom"] == "0"


def test_ptz_control_base_url_for_direct_camera():
    camera = IPCamera(
        id=1,
        name="PTZ Cam",
        model="test",
        serial_no="serial",
        input_port=1,
        connection_type="Direct",
        streams=[],
        support_ptz=True,
    )
    client = ISAPIClient(TEST_HOST, "u1", "p1")
    assert client._ptz_control_base_url(camera) == "PTZCtrl/channels/1"


def test_ptz_control_base_url_for_proxied_camera():
    camera = IPCamera(
        id=3,
        name="PTZ Cam",
        model="test",
        serial_no="serial",
        input_port=3,
        connection_type="Proxied",
        streams=[],
        support_ptz=True,
    )
    client = ISAPIClient(TEST_HOST, "u1", "p1")
    assert client._ptz_control_base_url(camera) == "ContentMgmt/PTZCtrlProxy/channels/3"