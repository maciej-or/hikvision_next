"""Tests for sensor platform."""

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_sensor_value(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test sensors value."""

    for entity_id, state in [
        ("sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_alarm_server_ipaddress", "1.0.0.159"),
        ("sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_alarm_server_portno", "8123"),
        ("sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_alarm_server_url", "/api/hikvision"),
        ("sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_alarm_server_protocoltype", "HTTP"),
        ("sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_1_hdd1", "OK"),
    ]:
        assert (sensor := hass.states.get(entity_id))
        assert sensor.state == state
