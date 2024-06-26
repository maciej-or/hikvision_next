"""Tests for camera platform."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.const import STATE_IDLE
from homeassistant.components import camera as camera_component
from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_camera(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test camera initialization."""

    assert len(hass.states.async_entity_ids(CAMERA_DOMAIN)) == 9

    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_101"
    assert (camera := hass.states.get(entity_id))
    assert camera.state == STATE_IDLE
    assert camera.name == "garden Main Stream"

    camera_entity = camera_component._get_camera_from_entity_id(hass, entity_id)
    stream_url = await camera_entity.stream_source()
    assert stream_url == "rtsp://u1:%2A%2A%2A@1.0.0.255:10554/Streaming/channels/101"
