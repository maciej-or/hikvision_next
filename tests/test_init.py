"""Tests for the hikvision_next integration."""

import pytest
from unittest.mock import patch
from homeassistant.core import HomeAssistant
from custom_components.hikvision_next.const import DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.config_entries import ConfigEntryState
from tests.conftest import TEST_CONFIG, TEST_CONFIG_WITH_ALARM_SERVER


@pytest.mark.parametrize("init_integration",
[
    "DS-7608NXI-I2",
    "DS-2CD2386G2-IU",
    "DS-2CD2146G2-ISU",
    "DS-2CD2443G0-IW",
    "DS-7616NI-K2",
    "DS-7616NI-Q2",
    "DS-7732NI-M4",
    "iDS-7204HUHI-M1"
], indirect=True)
async def test_basic_init(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test a successful setup entry."""

    entry = init_integration
    assert entry.state == ConfigEntryState.LOADED

    isapi = hass.data[DOMAIN][entry.entry_id]["isapi"]
    assert isapi.host == TEST_CONFIG["host"]
    assert init_integration.title in isapi.device_info.model


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_async_setup_entry_nvr(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test a successful NVR setup entry."""

    entry = init_integration
    assert entry.state == ConfigEntryState.LOADED

    isapi = hass.data[DOMAIN][entry.entry_id]["isapi"]
    assert isapi.host == TEST_CONFIG["host"]
    assert len(isapi.cameras) == 4
    assert len(isapi.supported_events) == 63

    device_info = isapi.device_info
    assert device_info.device_type == "NVR"
    assert device_info.firmware == "V4.62.210"
    assert device_info.input_ports == 4
    assert TEST_CONFIG["host"].endswith(device_info.ip_address)
    assert device_info.is_nvr is True
    assert len(device_info.mac_address) == 17
    assert device_info.manufacturer == "Hikvision"
    assert device_info.model == "DS-7608NXI-I2/8P/S"
    assert device_info.name == "nvr"
    assert device_info.output_ports == 1
    assert device_info.rtsp_port == "10554"
    assert device_info.serial_no == "DS-7608NXI-I0/0P/S0000000000CCRRJ00000000WCVU"
    assert len(device_info.storage) == 1
    assert device_info.support_alarm_server is True
    assert device_info.support_analog_cameras == 0
    assert device_info.support_channel_zero is True
    assert device_info.support_digital_cameras == 8
    assert device_info.support_event_mutex_checking is False
    assert device_info.support_holiday_mode is True

    # test successful unload
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_async_setup_entry_ipc(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test a successful IP camera setup entry."""

    entry = init_integration
    assert entry.state == ConfigEntryState.LOADED

    isapi = hass.data[DOMAIN][entry.entry_id]["isapi"]
    assert isapi.host == TEST_CONFIG["host"]
    assert len(isapi.cameras) == 1
    assert len(isapi.supported_events) == 14

    device_info = isapi.device_info
    assert device_info.device_type == "IPCamera"
    assert device_info.firmware == "V5.7.15"
    assert device_info.input_ports == 0
    assert TEST_CONFIG["host"].endswith(device_info.ip_address)
    assert device_info.is_nvr is False
    assert len(device_info.mac_address) == 17
    assert device_info.manufacturer == "Hikvision"
    assert device_info.model == "DS-2CD2386G2-IU"
    assert device_info.name == "yard"
    assert device_info.output_ports == 0
    assert device_info.output_ports == 0
    assert device_info.rtsp_port == "10554"
    assert device_info.serial_no == "DS-2CD2386G2-IU00000000AAWRJ00000000"
    assert len(device_info.storage) == 2
    assert device_info.support_alarm_server is True
    assert device_info.support_analog_cameras == 0
    assert device_info.support_channel_zero is False
    assert device_info.support_digital_cameras == 0
    assert device_info.support_event_mutex_checking is False
    assert device_info.support_holiday_mode is False

    # test successful unload
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.data.get(DOMAIN)


@pytest.mark.parametrize("mock_config_entry", [TEST_CONFIG_WITH_ALARM_SERVER], indirect=True)
@pytest.mark.parametrize("init_integration", [("DS-7608NXI-I2", True), ("DS-2CD2386G2-IU", True)], indirect=True)
async def test_async_setup_entry_nvr_with_alarm_server(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test a successful NVR setup entry with setting alarm server."""

    entry = init_integration

    with patch("custom_components.hikvision_next.isapi.ISAPI.set_alarm_server") as set_alarm_server_mock:
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state == ConfigEntryState.LOADED
        assert set_alarm_server_mock.call_args[0] == ("http://1.0.0.11:8123", "/api/hikvision")

        # test successful unload
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert set_alarm_server_mock.call_args[0] == ("http://0.0.0.0:80", "/")
        assert not hass.data.get(DOMAIN)
