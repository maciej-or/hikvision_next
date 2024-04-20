import respx
import pytest
from homeassistant.core import HomeAssistant
from custom_components.hikvision_next.const import DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.config_entries import ConfigEntryState
from tests.conftest import MOCK_CONFIG


@respx.mock
@pytest.mark.parametrize("mock_isapi_device", ["DS-7608NXI-I2"], indirect=True)
async def test_async_setup_entry_nvr(hass: HomeAssistant, mock_isapi_device) -> None:
    """Test a successful NVR setup entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**MOCK_CONFIG},
    )

    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.LOADED

    isapi = hass.data[DOMAIN][entry.entry_id]["isapi"]
    assert isapi.host == MOCK_CONFIG["host"]
    assert len(isapi.cameras) == 4
    assert len(isapi.supported_events) == 40

    device_info = isapi.device_info
    assert device_info.device_type == "NVR"
    assert device_info.firmware == "V4.62.210"
    assert device_info.input_ports == 4
    assert MOCK_CONFIG["host"].endswith(device_info.ip_address)
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
    assert device_info.support_channel_zero == "true"
    assert device_info.support_digital_cameras == 8
    assert device_info.support_event_mutex_checking == "false"
    assert device_info.support_holiday_mode == "true"


@respx.mock
@pytest.mark.parametrize("mock_isapi_device", ["DS-2CD2386G2-IU"], indirect=True)
async def test_async_setup_entry_ipc(hass: HomeAssistant, mock_isapi_device) -> None:
    """Test a successful IP camera setup entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**MOCK_CONFIG},
    )

    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.LOADED

    isapi = hass.data[DOMAIN][entry.entry_id]["isapi"]
    assert isapi.host == MOCK_CONFIG["host"]
    assert len(isapi.cameras) == 1
    assert len(isapi.supported_events) == 11

    device_info = isapi.device_info
    assert device_info.device_type == "IPCamera"
    assert device_info.firmware == "V5.7.15"
    assert device_info.input_ports == 0
    assert MOCK_CONFIG["host"].endswith(device_info.ip_address)
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
