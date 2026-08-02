"""Tests for switch platform."""

import respx
import pytest
import httpx
from homeassistant.core import HomeAssistant
from custom_components.hikvision_next.isapi.const import EVENT_IO
from custom_components.hikvision_next.hikvision_device import HikvisionDevice
from tests.conftest import TEST_HOST
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry
import homeassistant.helpers.entity_registry as er
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_ON, STATE_OFF


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_event_switch_state(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test switch state."""

    for entity_id, state in [
        ("switch.garden_video_loss_detection", STATE_ON),
        ("switch.garden_intrusion_detection", STATE_OFF),
        ("switch.garden_line_crossing_detection", STATE_ON),
        ("switch.garden_scene_change_detection", STATE_OFF),
        ("switch.home_motion_detection", STATE_OFF),
        ("switch.home_video_loss_detection", STATE_ON),
        ("switch.home_intrusion_detection", STATE_OFF),
        ("switch.home_line_crossing_detection", STATE_ON),
        ("switch.home_region_entrance_detection", STATE_ON),
        ("switch.home_scene_change_detection", STATE_OFF),
        ("switch.road_video_loss_detection", STATE_ON),
        ("switch.road_intrusion_detection", STATE_ON),
        ("switch.nvr_alarm_output_1", STATE_OFF),
        ("switch.nvr_holiday_mode", STATE_OFF),
    ]:
        assert (switch := hass.states.get(entity_id))
        assert switch.state == state

    entity_registry = er.async_get(hass)
    for entity_id in [
        "switch.home_region_exiting_detection",
        "switch.road_motion_detection",
        "switch.road_line_crossing_detection",
    ]:
        switch_entity = entity_registry.async_get(entity_id)
        assert switch_entity.disabled


@pytest.mark.parametrize("init_integration", ["DS-2CD2T86G2-ISU"], indirect=True)
async def test_event_switch_state_of_a_camera(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test switch state of a camera."""

    for entity_id, state in [
        ("switch.camera_3_intrusion_detection", STATE_ON),
        ("switch.camera_3_scene_change_detection", STATE_ON),
        ("switch.camera_3_alarm_input_1", STATE_OFF),
        ("switch.camera_3_alarm_output_1", STATE_OFF)
    ]:
        assert (switch := hass.states.get(entity_id))
        assert switch.state == state


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_event_switch_payload(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test event switch."""

    entity_id = "switch.garden_video_loss_detection"
    assert (switch := hass.states.get(entity_id))
    assert switch.state == STATE_ON

    def update_side_effect(request, route):
        payload = '<?xml version="1.0" encoding="utf-8"?>\n<VideoLoss version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema"><enabled>false</enabled></VideoLoss>'
        if request.content.decode("utf-8") != payload:
            raise AssertionError("Request content does not match expected payload")
        return httpx.Response(200)

    url = f"{TEST_HOST}/ISAPI/ContentMgmt/InputProxy/channels/1/video/videoLoss"
    endpoint = respx.put(url).mock(side_effect=update_side_effect)

    # do not call if already on
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert endpoint.called is False

    # switch to off
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert endpoint.called


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_alarm_output_switch(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test alarm output switch."""

    port_no = 1
    entity_id = f"switch.nvr_alarm_output_{port_no}"
    assert (switch := hass.states.get(entity_id))
    assert switch.state == STATE_OFF

    url = f"{TEST_HOST}/ISAPI/System/IO/outputs/{port_no}/trigger"
    endpoint = respx.put(url)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )

    assert endpoint.called


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2", "DS-7616NI-K2", "DS-2CD2146G2-ISU"], indirect=True)
async def test_alarm_input_switch_state_url(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test alarm output switch."""

    device: HikvisionDevice = init_integration.runtime_data
    for e in device.events_info:
        if e.id == EVENT_IO:
            if e.io_port_id < 100:
                assert e.url == f"System/IO/inputs/{e.io_port_id}"
            else:
                assert e.is_proxy
                assert e.url == f"ContentMgmt/IOProxy/inputs/{e.io_port_id}"


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2", "DS-7616NI-K2"], indirect=True)
async def test_nvr_event_switch_state_url(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test NVR event switch."""

    device: HikvisionDevice = init_integration.runtime_data
    for e in device.cameras[0].events_info:
        if e.id == "motiondetection":
            assert e.url == f"ContentMgmt/InputProxy/channels/{e.channel_id}/video/motionDetection"
        if e.id == "fielddetection":
            assert e.url == f"Smart/FieldDetection/{e.channel_id}"


@pytest.mark.parametrize("init_integration", ["iDS-7204HUHI-M1"], indirect=True)
async def test_dvr_event_switch_state_url(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test DVR event switch."""

    device: HikvisionDevice = init_integration.runtime_data
    for e in device.cameras[0].events_info:
        if e.id == "motiondetection":
            assert e.url == f"System/Video/inputs/channels/{e.channel_id}/motionDetection"
        if e.id == "fielddetection":
            assert e.url == f"Smart/FieldDetection/{e.channel_id}"


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_ipc_event_switch_state_url(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test IPC event switch."""

    device: HikvisionDevice = init_integration.runtime_data
    for e in device.cameras[0].events_info:
        if e.id == "motiondetection":
            assert e.url == f"System/Video/inputs/channels/{e.channel_id}/motionDetection"
        if e.id == "fielddetection":
            assert e.url == f"Smart/FieldDetection/{e.channel_id}"


@pytest.mark.parametrize("init_integration", ["DS-2SE4C425MWG-E-26"], indirect=True)
async def test_ipc_multichannel_event_switch(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test IPC two-channels events."""

    device: HikvisionDevice = init_integration.runtime_data

    assert device.capabilities.is_multi_channel
    assert len(device.cameras[0].events_info) == 2
    assert len(device.cameras[1].events_info) == 4

    switch_entities = [
        'switch.ip_dome_channel_1_motion_detection',
        'switch.ip_dome_channel_1_video_tampering_detection',
        'switch.ip_dome_channel_2_intrusion_detection',
        'switch.ip_dome_channel_2_line_crossing_detection',
        # 2 events are diabled
    ]
    for entity_id in switch_entities:
        assert hass.states.get(entity_id)
