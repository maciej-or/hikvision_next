"""Tests for sensor platform."""

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
import homeassistant.helpers.entity_registry as er


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_sensor_value(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test sensors value."""

    for entity_id, state in [
        ("sensor.nvr_notifications_host", "1.0.0.159"),
        ("sensor.nvr_notifications_host_port", "8123"),
        ("sensor.nvr_notifications_host_path", "/api/hikvision"),
        ("sensor.nvr_notifications_host_protocol", "HTTP"),
        ("sensor.nvr_sata_hdd1", "OK"),
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
        ("sensor.camera_3_notifications_host", "ha.hostname.domain"),
        ("sensor.camera_3_notifications_host_port", "443"),
        ("sensor.camera_3_notifications_host_path", "/api/hikvision"),
        ("sensor.camera_3_notifications_host_protocol", "HTTPS"),
        ("sensor.camera_3_sata_hdde", "OK"),
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
            "serial_no": "garden",
            "disabled": False,
        },
        "DS-2CD2146G2-ISU": {
            "serial_no": "ip_camera",
            "disabled": False,
        },
    }

    data = device_data[init_integration.title]
    entities = [
        f"binary_sensor.{data['serial_no']}_scene_change",
        f"switch.{data['serial_no']}_scene_change_detection"
    ]

    entity_registry = er.async_get(hass)
    for entity_id in entities:
        assert (entity := entity_registry.async_get(entity_id))
        assert entity.disabled == data["disabled"]
