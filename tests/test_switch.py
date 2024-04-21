"""Tests for switch platform."""

import respx
import pytest
import httpx
from homeassistant.core import HomeAssistant
from tests.conftest import TEST_CONFIG
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
)


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_event_switch_payload(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test event switch."""

    entity_id = "switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_1_videoloss"
    assert (switch := hass.states.get(entity_id))
    assert switch.state == STATE_ON

    def update_side_effect(request, route):
        payload = '<?xml version="1.0" encoding="utf-8"?>\n<VideoLoss version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema"><enabled>false</enabled></VideoLoss>'
        if request.content.decode("utf-8") != payload:
            raise AssertionError("Request content does not match expected payload")
        return httpx.Response(200)

    url = f"{TEST_CONFIG['host']}/ISAPI/ContentMgmt/InputProxy/channels/1/video/videoLoss"
    endpoint = respx.put(url).mock(side_effect=update_side_effect)

    # do not call if already on
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert endpoint.called is False

    # switch to off
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert endpoint.called
