"""Test event notifications."""

import pytest
from http import HTTPStatus
from homeassistant.core import HomeAssistant, Event
from custom_components.hikvision_next.notifications import EventNotificationsView
from custom_components.hikvision_next.const import HIKVISION_EVENT, RTSP_PORT_FORCED
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unittest.mock import MagicMock
from tests.conftest import load_fixture, TEST_HOST_IP, TEST_CONFIG, TEST_CONFIG_OUTSIDE_NETWORK
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


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_ipc_vmd_detection_alert(
    hass: HomeAssistant, init_integration: MockConfigEntry,
) -> None:
    """Test incoming motion detection event alert from ip camera."""

    entity_id = "binary_sensor.ds_2cd2386g2_iu00000000aawrj00000000_1_motiondetection"

    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_OFF

    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("vmd")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON

    mock_request = mock_event_notification("vmd_human")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON


@pytest.mark.parametrize("init_integration", ["DS-2TD1228-2-QA"], indirect=True)
async def test_ipc_motion_detection_on_thermometry_channel_alert(
    hass: HomeAssistant, init_integration: MockConfigEntry,
) -> None:
    """Test incoming motion detection event alert on thermometry channel from ip multi channel camera."""

    entity_id = "binary_sensor.ds_2td1228_2_qa_xxxxxxxxxxxxxxxxxx_2_motiondetection"

    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_OFF

    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("ipc_thermometry_motiondetection")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON


@pytest.mark.parametrize("init_integration", ["DS-2TD1228-2-QA"], indirect=True)
async def test_motion_detection_human_alert(
    hass: HomeAssistant, init_integration: MockConfigEntry,
) -> None:
    """Test incoming motion detection event alert with target type from ip multi channel camera."""

    entity_id = "binary_sensor.ds_2td1228_2_qa_xxxxxxxxxxxxxxxxxx_2_motiondetection"

    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_OFF
    assert sensor.attributes.get("target_type") == None

    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("motiondetection_human")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON
    assert sensor.attributes.get("target_type") == "human"


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


@pytest.mark.parametrize(
    "init_multi_device_integration",
    [
        [
            {"model": "DS-7608NXI-I2", "config": TEST_CONFIG},
            {"model": "DS-2CD2T46G2-ISU", "config": TEST_CONFIG_OUTSIDE_NETWORK},
            {"model": "DS-2CD2346G2-ISU", "config": {**TEST_CONFIG_OUTSIDE_NETWORK, RTSP_PORT_FORCED: 5152}},
            {"model": "DS-2CD2T86G2-ISU", "config": {**TEST_CONFIG_OUTSIDE_NETWORK, RTSP_PORT_FORCED: 5153}},
        ]
    ],
    indirect=True,
)
async def test_nvr_and_cam_notification_alert(
    hass: HomeAssistant,
    init_multi_device_integration: list[MockConfigEntry],
) -> None:
    """Test incoming multiple notifications with 1 NVR in the same network et 3 cameras outside."""

    """A NVR IN THE SAME NETWORK without macAddress in notification"""
    entity_nvr_1_id = "binary_sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_2_fielddetection"

    """ANOTHER CAMERAS OUTSIDE THE NETWORK with macAddress in notification"""
    entity_cam_1_id = "binary_sensor.ds_2cd2t46g2_isu_sl00000000aawrg00000000_1_io"
    entity_cam_2_id = "binary_sensor.ds_2cd2346g2_isu_sl00000000aawrj00000000_1_io"
    entity_cam_3_id = "binary_sensor.ds_2cd2t86g2_isu_sl00000000aawrae0000000_1_io"

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


def test_get_isapi_device_matches_by_normalized_serial_number():
    """Match by serial number when neither the source IP nor macAddress can be trusted.

    Some NVR firmware reports its own serial number slightly differently in event
    notifications (e.g. missing a hyphen) than in System/deviceInfo, and the source IP seen
    by HA can be rewritten by NAT/port-forwarding (e.g. Docker publishing a container port
    reports the bridge gateway as the peer, not the real device IP).
    """

    nvr_device = MagicMock()
    nvr_device.host = "http://10.100.199.62"
    nvr_device.device_info.mac_address = "08:cc:81:23:1d:d2"
    nvr_device.device_info.serial_no = "DS-7616NXI-I2/VPro-1620250716CCRRGC9285990WCVU"

    panel_device = MagicMock()
    panel_device.host = "http://10.100.199.58"
    panel_device.device_info.mac_address = "aa:bb:cc:dd:ee:ff"
    panel_device.device_info.serial_no = "DS-PHA96-MM-0000000000"

    nvr_entry = MagicMock(disabled_by=None, runtime_data=nvr_device)
    panel_entry = MagicMock(disabled_by=None, runtime_data=panel_device)

    view = EventNotificationsView(MagicMock())
    view.hass.config_entries.async_entries.return_value = [nvr_entry, panel_entry]

    device = view.get_isapi_device(
        "172.17.0.1",  # NAT-mangled source IP, matches neither configured device
        None,  # this event type doesn't include a macAddress
        "DS-7616NXI-I2/VPro1620250716CCRRGC9285990WCVU",  # missing hyphen vs. stored serial
    )

    assert device is nvr_device


class _EntryStillLoading:
    """A config entry that exists but hasn't reached "entry.runtime_data = device" yet
    (e.g. it's mid-(re)load at the exact moment another device's event arrives).
    """

    disabled_by = None


def test_get_isapi_device_skips_entries_without_runtime_data_yet():
    """A config entry mid-setup/reload has no "runtime_data" attribute at all, and accessing
    it directly used to raise AttributeError -- crashing this lookup and dropping the
    incoming event for every device, not just the one that was reloading.
    """

    nvr_device = MagicMock()
    nvr_device.host = "http://10.100.199.62"
    nvr_device.device_info.mac_address = "08:cc:81:23:1d:d2"
    nvr_device.device_info.serial_no = "DS-7616NXI-I2/VPro-1620250716CCRRGC9285990WCVU"
    nvr_entry = MagicMock(disabled_by=None, runtime_data=nvr_device)

    view = EventNotificationsView(MagicMock())
    view.hass.config_entries.async_entries.return_value = [nvr_entry, _EntryStillLoading()]

    device = view.get_isapi_device("10.100.199.62", "08:cc:81:23:1d:d2")

    assert device is nvr_device
