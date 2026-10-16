"""Tests for camera platform."""

import homeassistant.helpers.entity_registry as er
import httpx
import pytest
import respx
from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.components.camera.helper import get_camera_from_entity_id
from homeassistant.const import STATE_IDLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_next.camera import HikvisionWebRTCCamera
from custom_components.hikvision_next.camera import async_setup_entry as setup_cameras
from custom_components.hikvision_next.const import CONF_CONNECTION_SDK, CONF_SDK_WEBRTC
from custom_components.hikvision_next.hikvision_device import HikvisionDevice
from tests.conftest import TEST_HOST, load_fixture


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_camera(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test camera initialization."""

    assert len(hass.states.async_entity_ids(CAMERA_DOMAIN)) == 3

    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_101"
    assert hass.states.get(entity_id)

    camera_entity = get_camera_from_entity_id(hass, entity_id)
    assert camera_entity.state == STATE_IDLE
    assert camera_entity.name == "garden"

    stream_url = await camera_entity.stream_source()
    assert stream_url == "rtsp://u1:%2A%2A%2A@1.0.0.255:10554/Streaming/channels/101"

    entity_registry = er.async_get(hass)
    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_102"
    camera_entity = entity_registry.async_get(entity_id)
    assert camera_entity.disabled
    assert camera_entity.original_name == "Sub-Stream"

    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_104"
    camera_entity = entity_registry.async_get(entity_id)
    assert camera_entity.disabled
    assert camera_entity.original_name == "Transcoded Stream"


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_camera_snapshot(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test camera snapshot."""

    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_101"
    camera_entity = get_camera_from_entity_id(hass, entity_id)

    image_url = f"{TEST_HOST}/ISAPI/Streaming/channels/101/picture"
    respx.get(image_url).respond(content=b"binary image data")
    image = await camera_entity.async_camera_image()
    assert image == b"binary image data"


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_camera_snapshot_device_error(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test camera snapshot with 2 attempts."""

    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_101"
    camera_entity = get_camera_from_entity_id(hass, entity_id)

    image_url = f"{TEST_HOST}/ISAPI/Streaming/channels/101/picture"
    route = respx.get(image_url)
    error_response = load_fixture("ISAPI/Streaming.channels.x0y.picture", "deviceError")
    route.side_effect = [
        httpx.Response(200, content=error_response),
        httpx.Response(200, content=error_response),
        httpx.Response(200, content=b"binary image data"),
    ]
    image = await camera_entity.async_camera_image()
    assert image == b"binary image data"


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7616NI-Q2"], indirect=True)
async def test_camera_snapshot_alternate_url(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test camera snapshot with alternate url."""

    entity_id = "camera.ds_7616ni_q2_00p0000000000ccrre00000000wcvu_101"
    camera_entity = get_camera_from_entity_id(hass, entity_id)

    error_response = load_fixture("ISAPI/Streaming.channels.x0y.picture", "badXmlContent")
    image_url = f"{TEST_HOST}/ISAPI/Streaming/channels/101/picture"
    respx.get(image_url).respond(content=error_response)
    image_url = f"{TEST_HOST}/ISAPI/ContentMgmt/StreamingProxy/channels/101/picture"
    respx.get(image_url).respond(content=b"binary image data")
    image = await camera_entity.async_camera_image()
    assert image == b"binary image data"


device_data = {
    "DS-7608NXI-I2": {
        "entity_id": "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_101",
        "codec": "H.264",
        "width": 3840,
        "height": 2160,
        "rtsp_port": 10554,
    },
    "DS-7616NI-Q2": {
        "entity_id": "camera.ds_7616ni_q2_00p0000000000ccrre00000000wcvu_101",
        "codec": "H.265",
        "width": 2560,
        "height": 1440,
        "rtsp_port": 554,
    },
}

@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2", "DS-7616NI-Q2"], indirect=True)
async def test_camera_stream_info(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test camera snapshot with alternate url."""

    data = device_data[init_integration.title]
    entity_id = data["entity_id"]
    camera_entity = get_camera_from_entity_id(hass, entity_id)

    assert camera_entity.stream_info.codec == data["codec"]
    assert camera_entity.stream_info.width == data["width"]
    assert camera_entity.stream_info.height == data["height"]

    stream_url = await camera_entity.stream_source()
    assert stream_url == f"rtsp://u1:%2A%2A%2A@1.0.0.255:{data['rtsp_port']}/Streaming/channels/101"

@pytest.mark.parametrize("init_integration", ["DS-2TD1228-2-QA"], indirect=True)
async def test_camera_multichannel(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    entry = init_integration

    device: HikvisionDevice = entry.runtime_data
    assert len(device.cameras) == 2 # video channel + thermal channel
    assert device.cameras[0].input_port == 1
    assert device.cameras[1].input_port == 2


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2", "DS-7732NI-M4"], indirect=True)
async def test_nvr_with_onvif_cameras(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test proxy cameras with repeated serial no."""

    entry = init_integration
    device: HikvisionDevice = entry.runtime_data

    unique_serial_no = set()
    for camera in device.cameras:
        unique_serial_no.add(camera.serial_no)

    assert len(device.cameras) == len(unique_serial_no)


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2", "DS-7616NI-Q2"], indirect=True)
async def test_webrtc_opt_in_preserves_camera_identity(hass, init_integration):
    entry = init_integration
    device = entry.runtime_data
    device.connection_type = CONF_CONNECTION_SDK
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_SDK_WEBRTC: True})
    cameras = []
    await setup_cameras(hass, entry, cameras.extend)
    for camera in cameras:
        expected = camera.stream_info.codec == "H.264" and 1 <= camera.stream_info.type_id <= 11
        assert isinstance(camera, HikvisionWebRTCCamera) == expected
        registered = er.async_get(hass).async_get(camera.entity_id)
        assert registered.unique_id == camera.unique_id
        if hass.states.get(camera.entity_id):
            original = get_camera_from_entity_id(hass, camera.entity_id)
            assert camera.unique_id == original.unique_id
            assert await camera.stream_source() == await original.stream_source()
    # Opting in creates no native preview or peer until a viewer requests one.
    assert device.webrtc_manager is None


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_unload_closes_preview_before_sdk_logout(hass, init_integration):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    device = init_integration.runtime_data
    order = []

    async def close():
        order.append("previews_closed")

    device.webrtc_manager = SimpleNamespace(sessions={}, close=AsyncMock(side_effect=close))
    device.sdk_subscription = SimpleNamespace(NET_DVR_Logout_V30=Mock(side_effect=lambda user: order.append("logout")))
    device.sdk_user = 0
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    assert order == ["previews_closed", "logout"]
    assert device.webrtc_manager is None
