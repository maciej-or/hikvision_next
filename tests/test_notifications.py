"""Test event notifications."""

import pytest
import copy
from http import HTTPStatus
from homeassistant.core import HomeAssistant, Event
from custom_components.hikvision_next.notifications import EventNotificationsView
from custom_components.hikvision_next.const import HIKVISION_EVENT, DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unittest.mock import MagicMock
from tests.conftest import load_fixture, load_an_integ, TEST_HOST_IP, TEST_CONFIG, TEST_CLIENT_OUTSIDE_NETWORK
from homeassistant.const import (
    STATE_ON,
    STATE_OFF
)


def mock_event_notification(file) -> MagicMock:
    """Mock incoming event notification request."""

    mock_request = MagicMock()
    mock_request.headers = {
        'Content-Type': 'application/xml; charset="UTF-8"',
    }
    mock_request.remote = TEST_HOST_IP
    async def read():
        payload = load_fixture("ISAPI/EventNotificationAlert", file)
        return payload.encode()
    mock_request.read = read
    return mock_request


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_nvr_intrusion_detection_alert(
    hass: HomeAssistant, init_integration: MockConfigEntry,
) -> None:
    """Test incoming intrusion detection event alert from nvr."""

    entity_id = "binary_sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_2_fielddetection"
    bus_events = []
    def bus_event_listener(event: Event) -> None:
        bus_events.append(event)
    hass.bus.async_listen(HIKVISION_EVENT, bus_event_listener)

    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_OFF

    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("nvr_2_fielddetection")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON

    await hass.async_block_till_done()
    assert len(bus_events) == 1
    data = bus_events[0].data
    assert data["channel_id"] == 2
    assert data["event_id"] == "fielddetection"
    assert data["camera_name"] == "home"


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_ipc_intrusion_detection_alert(
    hass: HomeAssistant, init_integration: MockConfigEntry,
) -> None:
    """Test incoming intrusion detection event alert from ip camera."""

    entity_id = "binary_sensor.ds_2cd2386g2_iu00000000aawrj00000000_1_fielddetection"

    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_OFF

    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("ipc_1_fielddetection")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON


@pytest.mark.parametrize("init_integration", ["DS-2CD2146G2-ISU"], indirect=True)
async def test_field_detection_alert(
    hass: HomeAssistant, init_integration: MockConfigEntry,
) -> None:
    """Test incoming field detection event with detection target."""

    entity_id = "binary_sensor.ds_2cd2146g2_isu00000000aawrg00000000_1_fielddetection"
    bus_events = []
    def bus_event_listener(event: Event) -> None:
        bus_events.append(event)
    hass.bus.async_listen(HIKVISION_EVENT, bus_event_listener)

    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("fielddetection_human")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON

    await hass.async_block_till_done()
    assert len(bus_events) == 1
    data = bus_events[0].data
    assert data["channel_id"] == 1
    assert data["event_id"] == "fielddetection"
    assert data["detection_target"] == "human"
    assert data["region_id"] == 3

    mock_request = mock_event_notification("fielddetection_vehicle")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON

    await hass.async_block_till_done()
    assert len(bus_events) == 2
    data = bus_events[1].data
    assert data["channel_id"] == 1
    assert data["event_id"] == "fielddetection"
    assert data["detection_target"] == "vehicle"
    assert data["region_id"] == 2



@pytest.mark.parametrize("init_integration_outside_network", ["DS-2CD2T86G2-ISU"], indirect=True)
async def test_nvr_and_cam_notification_alert(
    hass: HomeAssistant, init_integration_outside_network: MockConfigEntry,
) -> None:
    """Test incoming multiple notifications with 1 NVR in the same network et 3 cameras outside."""
    """NOTE: Notification on NVR is without macAddress"""
    """NOTE: Notification on CAMERA is with macAddress"""
    
    """LOAD A NVR IN THE SAME NETWORK but without macAddress in notification"""
    model_nvr_1 = "DS-7608NXI-I2"
    config_nvr_1 =  copy.copy(TEST_CONFIG)
    entry_nvr_1 = MockConfigEntry(
        domain=DOMAIN,
        data=config_nvr_1,
        version=2
    )
    await load_an_integ(model_nvr_1, hass, entry_nvr_1)
    entity_nvr_1_id = "binary_sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_2_fielddetection"

    """LOAD ANOTHER CAMERA OUTSIDE THE NETWORK."""
    model_cam_2="DS-2CD2346G2-ISU"
    config_cam_2 = copy.copy(TEST_CLIENT_OUTSIDE_NETWORK)
    config_cam_2['rtsp_port_forced']=5153
    entry_cam_2= MockConfigEntry(
        domain=DOMAIN,
        data=config_cam_2,
        version=2
    )
    await load_an_integ(model_cam_2, hass, entry_cam_2)
    entity_cam_2_id = "binary_sensor.ds_2cd2346g2_isu_sl00000000aawrj00000000_1_1_io"

    """LOAD ANOTHER CAMERA OUTSIDE THE NETWORK."""
    model_cam_1="DS-2CD2T46G2-ISU"
    config_cam_1 = copy.copy(TEST_CLIENT_OUTSIDE_NETWORK)
    config_cam_1['rtsp_port_forced']=5153
    entry_cam_1= MockConfigEntry(
        domain=DOMAIN,
        data=config_cam_1,
        version=2
    )
    await load_an_integ(model_cam_1, hass, entry_cam_1)
    entity_cam_1_id = "binary_sensor.ds_2cd2t46g2_isu_sl00000000aawrg00000000_1_1_io"


    entity_cam_3_id = "binary_sensor.ds_2cd2t86g2_isu_sl00000000aawrae0000000_1_1_io"
    bus_events = []
    def bus_event_listener(event: Event) -> None:
        bus_events.append(event)
    hass.bus.async_listen(HIKVISION_EVENT, bus_event_listener)

    assert (sensor_cam_1 := hass.states.get(entity_cam_1_id))
    assert (sensor_cam_2 := hass.states.get(entity_cam_2_id))
    assert (sensor_cam_3 := hass.states.get(entity_cam_3_id))
    assert (sensor_nvr_1 := hass.states.get(entity_nvr_1_id))
    assert sensor_cam_1.state == STATE_OFF
    assert sensor_cam_2.state == STATE_OFF
    assert sensor_cam_3.state == STATE_OFF
    assert sensor_nvr_1.state == STATE_OFF

    
    """NOTIFICATION ON CAM 3 SENSOR"""
    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("cam3_DS-2CD2T86G2-ISU_io_notification")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    

    assert (sensor_cam_1 := hass.states.get(entity_cam_1_id))
    assert (sensor_cam_2 := hass.states.get(entity_cam_2_id))
    assert (sensor_cam_3 := hass.states.get(entity_cam_3_id))
    assert (sensor_nvr_1 := hass.states.get(entity_nvr_1_id))
    assert sensor_cam_1.state == STATE_OFF
    assert sensor_cam_2.state == STATE_OFF
    assert sensor_cam_3.state == STATE_ON
    assert sensor_nvr_1.state == STATE_OFF

    
    """NOTIFICATION ON CAM 1 SENSOR"""
    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("cam1_DS-2CD2T46G2-ISU_io_notification")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    

    assert (sensor_cam_1 := hass.states.get(entity_cam_1_id))
    assert (sensor_cam_2 := hass.states.get(entity_cam_2_id))
    assert (sensor_cam_3 := hass.states.get(entity_cam_3_id))
    assert (sensor_nvr_1 := hass.states.get(entity_nvr_1_id))
    assert sensor_cam_1.state == STATE_ON
    assert sensor_cam_2.state == STATE_OFF
    assert sensor_cam_3.state == STATE_ON
    assert sensor_nvr_1.state == STATE_OFF

    
    
    """NOTIFICATION WITHOUT MAC ADDRESS ON NVR"""
    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("nvr_2_fielddetection")
    response = await view.post(mock_request)
    
    assert response.status == HTTPStatus.OK
    

    assert (sensor_cam_1 := hass.states.get(entity_cam_1_id))
    assert (sensor_cam_2 := hass.states.get(entity_cam_2_id))
    assert (sensor_cam_3 := hass.states.get(entity_cam_3_id))
    assert (sensor_nvr_1 := hass.states.get(entity_nvr_1_id))
    assert sensor_cam_1.state == STATE_ON
    assert sensor_cam_2.state == STATE_OFF
    assert sensor_cam_3.state == STATE_ON
    assert sensor_nvr_1.state == STATE_ON