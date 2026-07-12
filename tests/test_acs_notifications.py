"""Tests for ACS door/lock notification routing."""

from unittest.mock import MagicMock

from homeassistant.const import STATE_ON

from custom_components.hikvision_next.isapi import AlertInfo
from custom_components.hikvision_next.notifications import EventNotificationsView


def test_device_level_lock_event_keeps_channel_zero():
    """ACS lock events must not be remapped to camera channel 1."""
    view = EventNotificationsView(MagicMock())
    device = MagicMock()
    device.device_info.is_nvr = False
    device.cameras = [MagicMock(id=1)]
    view.device = device

    alert = AlertInfo(0, 0, "lock", state=STATE_ON)
    view.update_alert_channel(alert)

    assert alert.channel_id == 0