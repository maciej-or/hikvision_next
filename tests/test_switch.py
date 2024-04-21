import respx
import pytest
import httpx
from custom_components.hikvision_next.const import DOMAIN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tests.conftest import MOCK_CONFIG
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
)


@respx.mock
@pytest.mark.parametrize("mock_isapi_device", ["DS-7608NXI-I2"], indirect=True)
async def test_event_switch_payload(hass: HomeAssistant, mock_isapi_device) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**MOCK_CONFIG},
    )

    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # EventInfo
    # id: 'videoloss'
    # channel_id: 1
    # unique_id: 'ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_1_videoloss'
    # url: 'ContentMgmt/InputProxy/channels/1/video/videoLoss'

    isapi = hass.data[DOMAIN][entry.entry_id]["isapi"]
    videoloss_event = next(
        (event for event in isapi.cameras[0].supported_events if event.id == "videoloss" and event.channel_id == 1),
        None,
    )
    assert videoloss_event

    entity_id = f"switch.{videoloss_event.unique_id}"
    assert (switch := hass.states.get(entity_id))
    assert switch.state == STATE_ON

    def update_side_effect(request, route):
        payload = '<?xml version="1.0" encoding="utf-8"?>\n<VideoLoss version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema"><enabled>false</enabled></VideoLoss>'
        if request.content.decode("utf-8") != payload:
            raise AssertionError("Request content does not match expected payload")
        return httpx.Response(200)

    url = f"{MOCK_CONFIG['host']}/ISAPI/{videoloss_event.url}"
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
