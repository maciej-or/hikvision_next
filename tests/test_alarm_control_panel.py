"""Tests for security control panel (SecurityCP) partitions and zones."""

import homeassistant.helpers.entity_registry as er
import pytest
import respx
from homeassistant.components.alarm_control_panel import DOMAIN as ALARM_DOMAIN
from homeassistant.components.alarm_control_panel.const import AlarmControlPanelState
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_ALARM_ARM_AWAY, SERVICE_ALARM_ARM_HOME, SERVICE_ALARM_DISARM
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_next.hikvision_device import HikvisionDevice
from custom_components.hikvision_next.notifications import EventNotificationsView
from tests.conftest import TEST_HOST
from tests.test_notifications import mock_event_notification

SERIAL = "ds_pha96_mm000000000000000000000"


@pytest.mark.parametrize("init_integration", ["DS-PHA96-MM"], indirect=True)
async def test_security_panel_entities_created(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test partitions and zones are created as entities, and no camera entities exist."""

    entity_registry = er.async_get(hass)
    for entity_id in [
        f"alarm_control_panel.{SERIAL}_partition_1",
        f"alarm_control_panel.{SERIAL}_partition_2",
        f"binary_sensor.{SERIAL}_zone_1",
        f"binary_sensor.{SERIAL}_zone_2",
        f"binary_sensor.{SERIAL}_zone_3",
    ]:
        assert (entity := entity_registry.async_get(entity_id))
        assert not entity.disabled

    # Partition 3 is disabled in the fixture -- no entity should be created for it.
    assert entity_registry.async_get(f"alarm_control_panel.{SERIAL}_partition_3") is None

    device: HikvisionDevice = init_integration.runtime_data
    assert device.device_info.is_security_panel
    assert not device.cameras


@pytest.mark.parametrize("init_integration", ["DS-PHA96-MM"], indirect=True)
async def test_security_panel_initial_state(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test initial state of partitions and zones reflects the mocked status."""

    home = hass.states.get(f"alarm_control_panel.{SERIAL}_partition_1")
    assert home.state == AlarmControlPanelState.ARMED_AWAY

    garage = hass.states.get(f"alarm_control_panel.{SERIAL}_partition_2")
    assert garage.state == AlarmControlPanelState.DISARMED

    # "status" == "trigger" drives the door sensor, not "magnetOpenStatus" (confirmed
    # against real hardware to be unreliable -- kept only as an informational attribute)
    # nor "alarm" (stays false while the partition is disarmed, as here).
    front_door = hass.states.get(f"binary_sensor.{SERIAL}_zone_1")
    assert front_door.state == "on"
    assert front_door.attributes["magnet_open_status"] is False

    living_room_pir = hass.states.get(f"binary_sensor.{SERIAL}_zone_2")
    assert living_room_pir.state == "on"

    garage_smoke = hass.states.get(f"binary_sensor.{SERIAL}_zone_3")
    assert garage_smoke.state == "off"
    assert hass.states.get(f"switch.{SERIAL}_zone_3_bypassed").state == "on"
    assert garage_smoke.attributes["battery_low"] is True


@pytest.mark.parametrize("init_integration", ["DS-PHA96-MM"], indirect=True)
async def test_panel_host_status_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Host-level diagnostics (AC power, tamper, backup battery, fault count, IP) reflect
    SecurityCP/status/host, independently of any specific zone/partition.
    """

    ac_power = hass.states.get(f"binary_sensor.{SERIAL}_ac_power")
    assert ac_power.state == "on"

    tamper = hass.states.get(f"binary_sensor.{SERIAL}_tamper")
    assert tamper.state == "on"

    battery = hass.states.get(f"sensor.{SERIAL}_battery")
    assert battery.state == "95"
    assert battery.attributes["status"] == "normal"

    battery_voltage = hass.states.get(f"sensor.{SERIAL}_battery_voltage")
    assert battery_voltage.state == "13.6"

    fault_count = hass.states.get(f"sensor.{SERIAL}_fault_count")
    assert fault_count.state == "2"

    ip_address = hass.states.get(f"sensor.{SERIAL}_ip_address")
    assert ip_address.state == "1.0.0.255"


@pytest.mark.parametrize("init_integration", ["DS-PHA96-MM"], indirect=True)
async def test_zone_telemetry_sensors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Telemetry sensors (battery/signal/temperature) are only created for zones that
    report them (wireless zones) -- the Front Door zone in the fixture does, the other two
    don't.
    """

    battery = hass.states.get(f"sensor.{SERIAL}_zone_1_battery")
    assert battery.state == "88"

    signal = hass.states.get(f"sensor.{SERIAL}_zone_1_signal")
    assert signal.state == "121"

    temperature = hass.states.get(f"sensor.{SERIAL}_zone_1_temperature")
    assert temperature.state == "24"

    for zone_id in (2, 3):
        for suffix in ("battery", "signal", "temperature"):
            assert hass.states.get(f"sensor.{SERIAL}_zone_{zone_id}_{suffix}") is None


@pytest.mark.parametrize("init_integration", ["DS-PHA96-MM"], indirect=True)
async def test_security_panel_arm_away(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test arming a partition away."""

    entity_id = f"alarm_control_panel.{SERIAL}_partition_2"
    url = f"{TEST_HOST}/ISAPI/SecurityCP/control/arm/2"
    endpoint = respx.put(url).respond(200)

    await hass.services.async_call(
        ALARM_DOMAIN,
        SERVICE_ALARM_ARM_AWAY,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert endpoint.called


@pytest.mark.parametrize("init_integration", ["DS-PHA96-MM"], indirect=True)
async def test_security_panel_arm_home(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test arming a partition home (stay)."""

    entity_id = f"alarm_control_panel.{SERIAL}_partition_2"
    url = f"{TEST_HOST}/ISAPI/SecurityCP/control/arm/2"
    endpoint = respx.put(url).respond(200)

    await hass.services.async_call(
        ALARM_DOMAIN,
        SERVICE_ALARM_ARM_HOME,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert endpoint.called


@pytest.mark.parametrize("init_integration", ["DS-PHA96-MM"], indirect=True)
async def test_security_panel_disarm(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test disarming a partition."""

    entity_id = f"alarm_control_panel.{SERIAL}_partition_1"
    url = f"{TEST_HOST}/ISAPI/SecurityCP/control/disarm/1"
    endpoint = respx.put(url).respond(200)

    await hass.services.async_call(
        ALARM_DOMAIN,
        SERVICE_ALARM_DISARM,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert endpoint.called


@pytest.mark.parametrize("init_integration", ["DS-PHA96-MM"], indirect=True)
async def test_security_panel_push_event_triggers_refresh(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    caplog,
) -> None:
    """Test a push event from a security panel is accepted and logged for calibration."""

    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("security_cp_zone_event")
    response = await view.post(mock_request)
    await hass.async_block_till_done()

    assert response.status == 200
    assert "Security control panel event" in caplog.text
