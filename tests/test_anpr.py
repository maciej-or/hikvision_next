"""Tests for ANPR sensor."""

import respx
import pytest
from http import HTTPStatus
from homeassistant.core import HomeAssistant, Event
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
import homeassistant.helpers.entity_registry as er
from custom_components.hikvision_next.const import HIKVISION_EVENT
from custom_components.hikvision_next.notifications import EventNotificationsView
from tests.test_notifications import mock_event_notification
from tests.conftest import TEST_HOST
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    STATE_ON,
    STATE_OFF
)


@pytest.mark.parametrize("init_integration", ["DS-2CD4A26FWD-IZS-P"], indirect=True)
async def test_anpr_entities(
    hass: HomeAssistant, init_integration: MockConfigEntry,
) -> None:
    """Test ANPR entities creation."""

    entities = [
        "binary_sensor.ds_2cd4a26fwd_izs_p00000000ccwr000000000_1_anpr",
        "switch.ds_2cd4a26fwd_izs_p00000000ccwr000000000_1_anpr",
    ]

    entity_registry = er.async_get(hass)
    for entity_id in entities:
        assert (entity := entity_registry.async_get(entity_id))
        assert not entity.disabled


@pytest.mark.parametrize("init_integration", ["DS-2CD4A26FWD-IZS-P"], indirect=True)
async def test_anpr_switch(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test ANPR switch."""

    entity_id = "switch.ds_2cd4a26fwd_izs_p00000000ccwr000000000_1_anpr"
    assert (switch := hass.states.get(entity_id))
    assert switch.state == STATE_ON

    url = f"{TEST_HOST}/ISAPI/Traffic/channels/1/vehicleDetect"
    endpoint = respx.put(url)

    # switch to off
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert endpoint.called


@pytest.mark.parametrize("init_integration", ["DS-2CD4A26FWD-IZS-P"], indirect=True)
async def test_anpr_alert(
    hass: HomeAssistant, init_integration: MockConfigEntry,
) -> None:
    """Test incoming ANPR alarm."""

    entity_id = "binary_sensor.ds_2cd4a26fwd_izs_p00000000ccwr000000000_1_anpr"
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_OFF

    bus_events = []
    def bus_event_listener(event: Event) -> None:
        bus_events.append(event)
    hass.bus.async_listen(HIKVISION_EVENT, bus_event_listener)

    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("ipc1_anpr_plate_detected")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK

    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON

    await hass.async_block_till_done()
    assert len(bus_events) == 1
    data = bus_events[0].data
    assert data["channel_id"] == 1
    assert data["event_id"] == "anpr"
    assert data["anpr_license_plate"] == "KH35192"
    assert data["anpr_direction"] == "forward"
    assert data["anpr_confidence_level"] == 100

    mock_request = mock_event_notification("ipc1_anpr_plate_unknown")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK

    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON

    await hass.async_block_till_done()
    assert len(bus_events) == 2
    data = bus_events[1].data
    assert data["channel_id"] == 1
    assert data["event_id"] == "anpr"
    assert data["anpr_license_plate"] == "unknown"
    assert data["anpr_direction"] == "forward"
    assert data["anpr_confidence_level"] == 0
