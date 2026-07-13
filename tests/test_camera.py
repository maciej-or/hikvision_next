"""Tests for camera platform."""

import pytest
import respx
import httpx
from homeassistant.core import HomeAssistant
from homeassistant.const import STATE_IDLE
from homeassistant.components.camera.helper import get_camera_from_entity_id
from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.components.stream import CONF_RTSP_TRANSPORT, CONF_USE_WALLCLOCK_AS_TIMESTAMPS
from custom_components.hikvision_next.hikvision_device import HikvisionDevice
from custom_components.hikvision_next.isapi import IPCamera, ISAPIClient
from custom_components.hikvision_next.isapi.const import CONNECTION_TYPE_PROXIED
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tests.conftest import load_fixture
from tests.conftest import TEST_HOST, mock_device_endpoints
import homeassistant.helpers.entity_registry as er


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
    assert stream_url == "rtsp://u1:%2A%2A%2A@1.0.0.255:10554/ISAPI/Streaming/channels/101"
    assert camera_entity.stream_options == {
        CONF_RTSP_TRANSPORT: "tcp",
        CONF_USE_WALLCLOCK_AS_TIMESTAMPS: True,
    }

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
        "width": "3840",
        "height": "2160",
        "rtsp_port": 10554,
    },
    "DS-7616NI-Q2": {
        "entity_id": "camera.ds_7616ni_q2_00p0000000000ccrre00000000wcvu_101",
        "codec": "H.265",
        "width": "2560",
        "height": "1440",
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
    assert stream_url == f"rtsp://u1:%2A%2A%2A@1.0.0.255:{data['rtsp_port']}/ISAPI/Streaming/channels/101"

@pytest.mark.parametrize("init_integration", ["DS-2TD1228-2-QA"], indirect=True)
async def test_camera_multichannel(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    entry = init_integration

    device: HikvisionDevice = entry.runtime_data
    assert len(device.cameras) == 2 # video channel + thermal channel
    assert device.cameras[0].input_port == 1
    assert device.cameras[1].input_port == 2


@respx.mock
async def test_proxied_thermal_camera_multichannel() -> None:
    """Test direct discovery of a thermal camera's second channel behind an NVR."""
    camera_host = "http://1.0.0.204"
    mock_device_endpoints("DS-2TD1228-2-QA", camera_host)

    client = ISAPIClient(TEST_HOST, "u1", "***")
    client.device_info.serial_no = "NVR-SERIAL"
    camera = IPCamera(
        id=12,
        name="Camera 01",
        model="DS-2TD1228-2/QA",
        serial_no="THERMAL-SERIAL",
        firmware="V5.5.98",
        input_port=1,
        connection_type=CONNECTION_TYPE_PROXIED,
        ip_addr="1.0.0.204",
    )

    cameras = await client.get_proxied_thermal_channels(camera, "HIKVISION")

    assert len(cameras) == 1
    assert cameras[0].id == 1202
    assert cameras[0].name == "Camera 02"
    assert cameras[0].serial_no == "NVR-SERIAL_HIKVISION_13"
    assert [stream.id for stream in cameras[0].streams] == [201, 202]
    assert cameras[0].streams[0].unique_id == "12_201"
    assert cameras[0].streams[0].source_host == camera_host
    assert client.get_stream_source(cameras[0].streams[0]) == (
        "rtsp://u1:%2A%2A%2A@1.0.0.204:554/ISAPI/Streaming/channels/201"
    )


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2", "DS-7732NI-M4"], indirect=True)
async def test_nvr_with_onvif_cameras(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test proxy cameras with repeated serial no."""

    entry = init_integration
    device: HikvisionDevice = entry.runtime_data

    unique_serial_no = set()
    for camera in device.cameras:
        unique_serial_no.add(camera.serial_no)

    assert len(device.cameras) == len(unique_serial_no)
