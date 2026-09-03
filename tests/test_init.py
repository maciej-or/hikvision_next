"""Tests for the hikvision_next integration."""

import pytest
from unittest.mock import patch
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from custom_components.hikvision_next import async_migrate_entry
from custom_components.hikvision_next.const import DOMAIN
from custom_components.hikvision_next.hikvision_device import HikvisionDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.config_entries import ConfigEntryState

from tests.conftest import TEST_CONFIG, TEST_CONFIG_WITH_ALARM_SERVER, TEST_CONFIG_OUTSIDE_NETWORK


@pytest.mark.parametrize("init_integration",
[
    "DS-7608NXI-I2",
    "DS-2CD2386G2-IU",
    "DS-2CD2146G2-ISU",
    "DS-2CD2443G0-IW",
    "DS-2CD2532F-IWS",
    "DS-2TD1228-2-QA",
    "DS-2CD2346G2-ISU",
    "DS-2CD2T46G2-ISU",
    "DS-2CD2T86G2-ISU",
    "DS-2SE4C425MWG-E-26",
    "DS-7616NI-K2",
    "DS-7616NI-Q2",
    "DS-7732NI-M4",
    "iDS-7204HUHI-M1",
    "iDS-7208HQHI-M1"
], indirect=True)

async def test_basic_init(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test a successful setup entry."""

    entry = init_integration
    assert entry.state == ConfigEntryState.LOADED

    device: HikvisionDevice = entry.runtime_data
    assert device.host == TEST_CONFIG["host"]
    assert init_integration.title in device.device_info.model


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_async_setup_entry_nvr(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test a successful NVR setup entry."""

    entry = init_integration
    assert entry.state == ConfigEntryState.LOADED

    device: HikvisionDevice = entry.runtime_data
    assert device.host == TEST_CONFIG["host"]
    assert len(device.cameras) == 4
    assert len(device.supported_events) == 63

    device_info = device.device_info
    capabilities = device.capabilities
    assert device_info.device_type == "NVR"
    assert device_info.firmware == "V4.62.210"
    assert capabilities.input_ports == 4
    assert TEST_CONFIG["host"].endswith(device_info.ip_address)
    assert device_info.is_nvr is True
    assert len(device_info.mac_address) == 17
    assert device_info.manufacturer == "Hikvision"
    assert device_info.model == "DS-7608NXI-I2/8P/S"
    assert device_info.name == "nvr"
    assert capabilities.output_ports == 1
    assert device.protocols.rtsp_port == "10554"
    assert device_info.serial_no == "DS-7608NXI-I0/0P/S0000000000CCRRJ00000000WCVU"
    assert len(device.storage) == 1
    assert capabilities.support_alarm_server is True
    assert capabilities.analog_cameras_inputs == 0
    assert capabilities.support_channel_zero is True
    assert capabilities.digital_cameras_inputs == 8
    assert capabilities.is_multi_channel is False
    assert capabilities.support_event_mutex_checking is False
    assert capabilities.support_holiday_mode is True

    # test successful unload
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_async_setup_entry_ipc(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test a successful IP camera setup entry."""

    entry = init_integration
    assert entry.state == ConfigEntryState.LOADED

    device: HikvisionDevice = entry.runtime_data
    assert device.host == TEST_CONFIG["host"]
    assert len(device.cameras) == 1
    assert len(device.supported_events) == 14

    device_info = device.device_info
    capabilities = device.capabilities
    assert device_info.device_type == "IPCamera"
    assert device_info.firmware == "V5.7.15"
    assert capabilities.input_ports == 0
    assert TEST_CONFIG["host"].endswith(device_info.ip_address)
    assert device_info.is_nvr is False
    assert len(device_info.mac_address) == 17
    assert device_info.manufacturer == "Hikvision"
    assert device_info.model == "DS-2CD2386G2-IU"
    assert device_info.name == "yard"
    assert capabilities.output_ports == 0
    assert device.protocols.rtsp_port == "10554"
    assert device_info.serial_no == "DS-2CD2386G2-IU00000000AAWRJ00000000"
    assert len(device.storage) == 2
    assert capabilities.support_alarm_server is True
    assert capabilities.analog_cameras_inputs == 0
    assert capabilities.support_channel_zero is False
    assert capabilities.digital_cameras_inputs == 0
    assert capabilities.is_multi_channel is False
    assert capabilities.support_event_mutex_checking is False
    assert capabilities.support_holiday_mode is False

    # test successful unload
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)


@pytest.mark.parametrize("mock_config_entry", [TEST_CONFIG_WITH_ALARM_SERVER], indirect=True)
@pytest.mark.parametrize("init_integration", [("DS-7608NXI-I2", True), ("DS-2CD2386G2-IU", True)], indirect=True)
async def test_async_setup_entry_nvr_with_alarm_server(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test a successful NVR setup entry with setting alarm server."""

    entry = init_integration

    with patch("custom_components.hikvision_next.isapi.ISAPIClient.set_alarm_server") as set_alarm_server_mock:
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state == ConfigEntryState.LOADED
        assert set_alarm_server_mock.call_args[0] == ("http://1.0.0.11:8123", "/api/hikvision")

        # test successful unload
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert set_alarm_server_mock.call_args[0] == ("http://0.0.0.0:80", "/")
        assert not hass.data.get(DOMAIN)


@pytest.mark.parametrize("mock_isapi", [TEST_CONFIG_OUTSIDE_NETWORK['host']], indirect=True)
@pytest.mark.parametrize("mock_config_entry", [TEST_CONFIG_OUTSIDE_NETWORK], indirect=True)
@pytest.mark.parametrize("init_integration", [("DS-2CD2T86G2-ISU")], indirect=True)
async def test_async_setup_entry_nvr_outside_network(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test a successful IP camera setup entry outside network."""

    entry = init_integration
    assert entry.state == ConfigEntryState.LOADED

    device: HikvisionDevice = entry.runtime_data
    assert device.host == TEST_CONFIG_OUTSIDE_NETWORK["host"]
    assert len(device.cameras) == 1
    assert len(device.supported_events) == 15

    device_info = device.device_info
    capabilities = device.capabilities
    assert device_info.device_type == "IPCamera"
    assert device_info.firmware == "V5.7.18"
    assert capabilities.input_ports == 1
    assert TEST_CONFIG_OUTSIDE_NETWORK["host"].endswith(device_info.ip_address)
    assert device_info.is_nvr is False
    assert len(device_info.mac_address) == 17
    assert device_info.manufacturer == "Hikvision"
    assert device_info.model == "DS-2CD2T86G2-ISU/SL"
    assert device_info.name == "CAMERA 3"
    assert capabilities.output_ports == 1
    assert device.protocols.rtsp_port == "5151"
    assert device_info.serial_no == "DS-2CD2T86G2-ISU/SL00000000AAWRAE0000000"
    assert len(device.storage) == 1
    assert capabilities.support_alarm_server is True
    assert capabilities.analog_cameras_inputs == 0
    assert capabilities.support_channel_zero is False
    assert capabilities.digital_cameras_inputs == 0
    assert capabilities.support_event_mutex_checking is False
    assert capabilities.support_holiday_mode is False

    # test successful unload
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2", "iDS-7204HUHI-M1", "DS-2CD2386G2-IU"], indirect=True)
async def test_device_info_without_deprecated_via_device(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test device info does not carry the via_device key removed in HA 2026.9."""

    device: HikvisionDevice = init_integration.runtime_data

    assert "via_device" not in device.hass_device_info()
    for camera in device.cameras:
        assert "via_device" not in device.hass_device_info(camera.id)


@pytest.mark.parametrize(
    ("init_integration", "linked_cameras"),
    [("DS-7608NXI-I2", 3), ("DS-7616NI-Q2", 9)],
    indirect=["init_integration"],
)
async def test_nvr_camera_devices_are_linked(
    hass: HomeAssistant, init_integration: MockConfigEntry, linked_cameras: int
) -> None:
    """Test camera devices of an NVR are nested under the NVR device."""

    entry = init_integration
    device: HikvisionDevice = entry.runtime_data
    device_registry = dr.async_get(hass)

    nvr_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, device.device_info.serial_no), entry.entry_id
    )
    assert nvr_device
    assert nvr_device.via_device_id is None
    assert device.root_device_id == nvr_device.id

    linked = 0
    for camera in device.cameras:
        # a channel without entities has no device entry, ie. one with no accessible streams
        if camera_device := device_registry.async_get_device_by_identifier(
            (DOMAIN, camera.serial_no), entry.entry_id
        ):
            assert camera_device.via_device_id == nvr_device.id
            linked += 1

    assert linked == linked_cameras


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_ip_camera_device_is_not_linked(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test a standalone IP camera is not nested under another device."""

    entry = init_integration
    device: HikvisionDevice = entry.runtime_data
    device_registry = dr.async_get(hass)

    for camera in device.cameras:
        assert "via_device_id" not in device.hass_device_info(camera.id)

    for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        assert device_entry.via_device_id is None


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_camera_is_not_its_own_via_device(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test a channel reporting the NVR serial is not linked to itself.

    Home Assistant raises when a device references itself as its via device.
    """

    device: HikvisionDevice = init_integration.runtime_data
    camera = device.cameras[0]
    camera.serial_no = device.device_info.serial_no

    assert "via_device_id" not in device.hass_device_info(camera.id)


async def test_migrate_entry_from_version_1(hass: HomeAssistant) -> None:
    """Test migration of a config entry created before version 2.

    Config entry attributes are read only, they can only be set through
    async_update_entry, otherwise the migration fails and the entry never loads.
    """

    serial_no = "DS-7608NXI-I0/0P/S0000000000CCRRJ00000000WCVU"
    entry = MockConfigEntry(domain=DOMAIN, data=TEST_CONFIG, version=1, unique_id=serial_no)
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 3
    assert entry.unique_id == serial_no


@pytest.mark.parametrize(
    "init_integration",
    [("DS-7608NXI-I2", True), ("iDS-7208HQHI-M1", True)],
    indirect=True,
)
async def test_setup_without_entity_id_deprecation_warnings(
    hass: HomeAssistant, init_integration: MockConfigEntry, caplog: pytest.LogCaptureFixture
) -> None:
    """Test setup does not hit any of the deprecations reported in issues #365, #366, #368.

    Home Assistant makes them fatal in 2027.8, 2027.2 and 2027.5 respectively. The
    integration is set up here, not by the fixture, so that caplog sees the warnings.
    """

    entry = init_integration
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state == ConfigEntryState.LOADED

    for message in (
        "deprecated `via_device` parameter",
        "sets an invalid entity ID",
        "sets an entity ID with wrong domain",
    ):
        assert message not in caplog.text
