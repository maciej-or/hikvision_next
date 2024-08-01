"""Tests for PIR sensor."""

import respx
import pytest
from http import HTTPStatus
from homeassistant.core import HomeAssistant
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry
import homeassistant.helpers.entity_registry as er
from custom_components.hikvision_next.notifications import EventNotificationsView
from tests.test_notifications import mock_event_notification
from tests.conftest import TEST_CONFIG
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    STATE_ON,
    STATE_OFF
)

@pytest.mark.parametrize("init_integration", ["DS-2CD2443G0-IW"], indirect=True)
async def test_pir_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test PIR entities creation """

    entities = [
        "binary_sensor.ds_2cd2443g0_iw00000000aawre00000000_1_pir",
        "switch.ds_2cd2443g0_iw00000000aawre00000000_1_pir"
    ]

    entity_registry = er.async_get(hass)
    for entity_id in entities:
        assert (entity := entity_registry.async_get(entity_id))
        assert not entity.disabled


@pytest.mark.parametrize("init_integration", ["DS-2CD2443G0-IW"], indirect=True)
async def test_pir_alert(
    hass: HomeAssistant, init_integration: MockConfigEntry,
) -> None:
    """Test incoming PIR alarm."""

    entity_id = "binary_sensor.ds_2cd2443g0_iw00000000aawre00000000_1_pir"
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_OFF

    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("pir")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON


@pytest.mark.parametrize("init_integration", ["DS-2CD2443G0-IW"], indirect=True)
async def test_pir_switch(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test PIR switch."""

    entity_id = "switch.ds_2cd2443g0_iw00000000aawre00000000_1_pir"
    assert (switch := hass.states.get(entity_id))
    assert switch.state == STATE_ON

    url = f"{TEST_CONFIG['host']}/ISAPI/WLAlarm/PIR"
    endpoint = respx.put(url)

    # switch to off
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert endpoint.called
