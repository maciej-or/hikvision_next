"""Tests for ISAPI PTZ endpoint and payload fallbacks."""

import pytest
import respx

from custom_components.hikvision_next.isapi import IPCamera
from custom_components.hikvision_next.isapi.isapi import ISAPIClient
from tests.conftest import TEST_HOST, mock_device_endpoints


@respx.mock
async def test_ptz_continuous_falls_back_to_capitalized_path() -> None:
    """ptz_continuous tries Continuous when lowercase continuous is unavailable."""
    mock_device_endpoints("DS-2SE4C425MWG-E-26")
    lower = f"{TEST_HOST}/ISAPI/PTZCtrl/channels/1/continuous"
    upper = f"{TEST_HOST}/ISAPI/PTZCtrl/channels/1/Continuous"
    respx.route(method="PUT", url=lower).respond(status_code=404)
    route = respx.route(method="PUT", url=upper).respond(text="<ResponseStatus>ok</ResponseStatus>")

    client = ISAPIClient(TEST_HOST, "u1", "p1")
    await client.get_hardware_info()
    camera = next(cam for cam in client.cameras if cam.id == 1)

    await client.ptz_continuous(camera, pan=60, tilt=0, zoom=0)

    assert route.called


@respx.mock
async def test_ptz_stop_tries_stop_endpoint() -> None:
    """ptz_stop prefers the dedicated stop endpoint when available."""
    mock_device_endpoints("DS-2SE4C425MWG-E-26")
    stop_url = f"{TEST_HOST}/ISAPI/PTZCtrl/channels/1/stop"
    route = respx.route(method="PUT", url=stop_url).respond(text="<ResponseStatus>ok</ResponseStatus>")

    client = ISAPIClient(TEST_HOST, "u1", "p1")
    await client.get_hardware_info()
    camera = next(cam for cam in client.cameras if cam.id == 1)

    await client.ptz_stop(camera)

    assert route.called