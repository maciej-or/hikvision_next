"""Tests for ACS lock entity."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant

from custom_components.hikvision_next.const import DOMAIN
from custom_components.hikvision_next.isapi import AlertInfo, EventInfo
from custom_components.hikvision_next.lock import AcsLockEntity
from custom_components.hikvision_next.notifications import EventNotificationsView


async def test_acs_lock_entity_updates_from_sdk_feedback(hass: HomeAssistant) -> None:
    device = MagicMock()
    device.device_info.serial_no = "DS-K1T6QT"
    device.hass_device_info.return_value = {}

    event = EventInfo(
        id="lock",
        channel_id=0,
        io_port_id=0,
        unique_id="ds_k1t6qt_lock",
        url="AccessControl/RemoteControl/door/1",
    )
    entity = AcsLockEntity(device, 0, event)
    hass.data.setdefault(DOMAIN, {})["event_lock_entities"] = {entity.unique_id: entity}

    view = EventNotificationsView(hass)
    view.device = device
    view.trigger_sensor(AlertInfo(0, 0, "lock", state=STATE_ON))
    assert entity.is_locked is False

    view.trigger_sensor(AlertInfo(0, 0, "lock", state=STATE_OFF))
    assert entity.is_locked is True


async def test_acs_lock_unlock_calls_remote_control(hass: HomeAssistant) -> None:
    device = MagicMock()
    device.device_info.serial_no = "DS-K1T6QT"
    device.hass_device_info.return_value = {}
    device.set_event_enabled_state = AsyncMock()

    event = EventInfo(
        id="lock",
        channel_id=0,
        io_port_id=0,
        unique_id="ds_k1t6qt_lock",
        url="AccessControl/RemoteControl/door/1",
    )
    entity = AcsLockEntity(device, 0, event)
    entity.hass = hass
    entity.platform = MagicMock()

    await entity.async_unlock()
    device.set_event_enabled_state.assert_awaited_once_with(0, event, True)

    device.set_event_enabled_state.reset_mock()
    await entity.async_lock()
    device.set_event_enabled_state.assert_awaited_once_with(0, event, False)


def test_lock_event_platform_unique_id():
    view = EventNotificationsView(MagicMock())
    view.device = MagicMock()
    view.device.device_info.is_nvr = False
    view.device.device_info.serial_no = "DS-K1T6QT"
    view.device.cameras = []

    unique_id = view._event_platform_unique_id(AlertInfo(0, 0, "lock"))
    assert unique_id == "lock.ds_k1t6qt_lock"