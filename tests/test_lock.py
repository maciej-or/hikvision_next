"""Tests for ACS lock entity."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant

from custom_components.hikvision_next.const import DOMAIN
from custom_components.hikvision_next.door_control import DoorControlAction
from custom_components.hikvision_next.isapi import AlertInfo, EventInfo
from custom_components.hikvision_next.lock import (
    AcsAlwaysOpenLockEntity,
    AcsMomentaryLockEntity,
)
from custom_components.hikvision_next.notifications import EventNotificationsView


@pytest.fixture
def lock_event() -> EventInfo:
    return EventInfo(
        id="lock",
        channel_id=0,
        io_port_id=0,
        unique_id="ds_k1t6qt_lock",
        url="AccessControl/RemoteControl/door/1",
    )


async def test_acs_lock_entities_share_state_updates(
    hass: HomeAssistant,
    lock_event: EventInfo,
) -> None:
    device = MagicMock()
    device.device_info.serial_no = "DS-K1T6QT"
    device.hass_device_info.return_value = {}

    momentary = AcsMomentaryLockEntity(device, 0, lock_event)
    always_open = AcsAlwaysOpenLockEntity(device, 0, lock_event)
    momentary.hass = hass
    always_open.hass = hass
    hass.data.setdefault(DOMAIN, {})["event_lock_groups"] = {
        lock_event.unique_id: [momentary, always_open],
    }

    momentary.set_locked(False)
    assert momentary.is_locked is False
    assert always_open.is_locked is False

    momentary.set_locked(True)
    assert momentary.is_locked is True
    assert always_open.is_locked is True


async def test_acs_lock_entity_updates_from_sdk_feedback(
    hass: HomeAssistant,
    lock_event: EventInfo,
) -> None:
    device = MagicMock()
    device.device_info.serial_no = "DS-K1T6QT"
    device.hass_device_info.return_value = {}

    momentary = AcsMomentaryLockEntity(device, 0, lock_event)
    always_open = AcsAlwaysOpenLockEntity(device, 0, lock_event)
    momentary.hass = hass
    always_open.hass = hass
    hass.data.setdefault(DOMAIN, {})["event_lock_groups"] = {
        lock_event.unique_id: [momentary, always_open],
    }

    view = EventNotificationsView(hass)
    view.device = device
    view.trigger_sensor(AlertInfo(0, 0, "lock", state=STATE_ON))
    assert momentary.is_locked is False
    assert always_open.is_locked is False

    view.trigger_sensor(AlertInfo(0, 0, "lock", state=STATE_OFF))
    assert momentary.is_locked is True
    assert always_open.is_locked is True


async def test_momentary_lock_unlock_calls_remote_control(
    hass: HomeAssistant,
    lock_event: EventInfo,
) -> None:
    device = MagicMock()
    device.device_info.serial_no = "DS-K1T6QT"
    device.hass_device_info.return_value = {}
    device.remote_control_door = AsyncMock()

    entity = AcsMomentaryLockEntity(device, 0, lock_event)
    entity.hass = hass
    entity.platform = MagicMock()

    await entity.async_unlock()
    device.remote_control_door.assert_awaited_once_with(
        lock_event, DoorControlAction.OPEN
    )

    device.remote_control_door.reset_mock()
    await entity.async_lock()
    device.remote_control_door.assert_awaited_once_with(
        lock_event, DoorControlAction.CLOSE
    )


async def test_always_open_lock_calls_remote_control(
    hass: HomeAssistant,
    lock_event: EventInfo,
) -> None:
    device = MagicMock()
    device.device_info.serial_no = "DS-K1T6QT"
    device.hass_device_info.return_value = {}
    device.remote_control_door = AsyncMock()

    entity = AcsAlwaysOpenLockEntity(device, 0, lock_event)
    entity.hass = hass
    entity.platform = MagicMock()

    await entity.async_unlock()
    device.remote_control_door.assert_awaited_once_with(
        lock_event, DoorControlAction.ALWAYS_OPEN
    )

    device.remote_control_door.reset_mock()
    await entity.async_lock()
    device.remote_control_door.assert_awaited_once_with(
        lock_event, DoorControlAction.RESTORE_NORMAL
    )


def test_lock_event_platform_unique_id():
    view = EventNotificationsView(MagicMock())
    view.device = MagicMock()
    view.device.device_info.is_nvr = False
    view.device.device_info.serial_no = "DS-K1T6QT"
    view.device.cameras = []

    unique_id = view._event_platform_unique_id(AlertInfo(0, 0, "lock"))
    assert unique_id == "lock.ds_k1t6qt_lock"