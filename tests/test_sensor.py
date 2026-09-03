"""Tests for sensor platform."""

import pytest
from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.util import slugify
from pytest_homeassistant_custom_component.common import MockConfigEntry
import homeassistant.helpers.entity_registry as er


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_sensor_value(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test sensors value."""

    for entity_id, state in [
        ("sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_alarm_server_address", "1.0.0.159"),
        ("sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_alarm_server_port_no", "8123"),
        ("sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_alarm_server_path", "/api/hikvision"),
        ("sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_alarm_server_protocol_type", "HTTP"),
        ("sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_1_hdd1", "OK"),
    ]:
        assert (sensor := hass.states.get(entity_id))
        assert sensor.state == state

@pytest.mark.parametrize("init_integration", ["DS-2CD2T86G2-ISU"], indirect=True)
async def test_sensor_value_outside_network(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test sensors value."""

    for entity_id, state in [
        ("sensor.ds_2cd2t86g2_isu_sl00000000aawrae0000000_alarm_server_address", "ha.hostname.domain"),
        ("sensor.ds_2cd2t86g2_isu_sl00000000aawrae0000000_alarm_server_port_no", "443"),
        ("sensor.ds_2cd2t86g2_isu_sl00000000aawrae0000000_alarm_server_path", "/api/hikvision"),
        ("sensor.ds_2cd2t86g2_isu_sl00000000aawrae0000000_alarm_server_protocol_type", "HTTPS"),
        ("sensor.ds_2cd2t86g2_isu_sl00000000aawrae0000000_1_hdde", "OK"),
    ]:
        assert (sensor := hass.states.get(entity_id))
        assert sensor.state == state


@pytest.mark.parametrize("init_integration", ["DS-2CD2146G2-ISU", "DS-7608NXI-I2"], indirect=True)
async def test_scenechange_support(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test sensors value."""

    device_data = {
        "DS-7608NXI-I2": {
            "serial_no": "ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu",
            "disabled": False,
        },
        "DS-2CD2146G2-ISU": {
            "serial_no": "ds_2cd2146g2_isu00000000aawrg00000000",
            "disabled": False,
        },
    }

    data = device_data[init_integration.title]
    entities = [
        f"binary_sensor.{data['serial_no']}_1_scenechangedetection",
        f"switch.{data['serial_no']}_1_scenechangedetection"
    ]

    entity_registry = er.async_get(hass)
    for entity_id in entities:
        assert (entity := entity_registry.async_get(entity_id))
        assert entity.disabled == data["disabled"]


@pytest.mark.parametrize(
    "init_integration",
    ["DS-7608NXI-I2", "DS-2CD2T86G2-ISU", "iDS-7208HQHI-M1"],
    indirect=True,
)
async def test_sensor_entity_ids_are_valid(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test sensor entity ids are valid for serial numbers with slashes and parentheses.

    The unique_id keeps the raw serial number, only the entity_id is slugified.
    """

    entity_registry = er.async_get(hass)
    entities = [
        entity
        for entity in er.async_entries_for_config_entry(entity_registry, init_integration.entry_id)
        if entity.domain == "sensor"
    ]
    assert entities

    for entity in entities:
        assert valid_entity_id(entity.entity_id)
        assert entity.entity_id == f"sensor.{slugify(entity.unique_id)}"
