"""Tests for coordinator helper logic."""

from dataclasses import dataclass
from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.hikvision_next.coordinator import (
    SecurityArmingListener,
    _resolve_update_interval,
    _zone_is_triggered,
)


@dataclass
class _FakeZoneStatus:
    status: str
    alarm: bool
    detector_type: str
    magnet_open_status: bool | None = None
    tamper_evident: bool = False


def test_status_trigger_wins_even_when_magnet_open_status_is_stuck_false():
    """Reproduces a real AX Hybrid PRO wireless magnetic contact zone.

    Opening/closing the physical contact reliably flipped "status" between "online" and
    "trigger", but "magnetOpenStatus" stayed false the whole time -- that field isn't
    populated correctly for this detector/module combination, so "status" must take
    priority for the sensor to ever reflect the door opening.
    """
    zone = _FakeZoneStatus(status="trigger", alarm=False, detector_type="magneticContact", magnet_open_status=False)
    assert _zone_is_triggered(zone) is True


def test_magnet_open_status_is_ignored_even_when_true():
    """A second real zone reported magnetOpenStatus stuck at True regardless of actual
    open/close, while "status" correctly tracked it -- magnetOpenStatus must not be trusted
    even when it happens to agree, since on this hardware it never actually moves.
    """
    zone = _FakeZoneStatus(status="online", alarm=False, detector_type="magneticContact", magnet_open_status=True)
    assert _zone_is_triggered(zone) is False


def test_non_door_zone_uses_status_not_alarm():
    zone = _FakeZoneStatus(status="trigger", alarm=False, detector_type="smokeDetector")
    assert _zone_is_triggered(zone) is True

    zone_off = _FakeZoneStatus(status="online", alarm=True, detector_type="smokeDetector")
    assert _zone_is_triggered(zone_off) is False


def test_tamper_detector_uses_tamper_evident_not_status():
    """A real tamperDetector zone reported "status" stuck at "online" regardless of tamper
    state -- "tamperEvident" is what actually reflects it for this detector type.
    """
    zone = _FakeZoneStatus(status="online", alarm=False, detector_type="tamperDetector", tamper_evident=True)
    assert _zone_is_triggered(zone) is True

    zone_off = _FakeZoneStatus(status="online", alarm=False, detector_type="tamperDetector", tamper_evident=False)
    assert _zone_is_triggered(zone_off) is False


def test_resolve_update_interval():
    default = timedelta(seconds=30)

    # Unset: keep the device type's own default.
    assert _resolve_update_interval(None, default) == default

    # 0: disable automatic polling entirely (push/manual refresh only).
    assert _resolve_update_interval(0, default) is None

    # Positive value: override the default.
    assert _resolve_update_interval(10, default) == timedelta(seconds=10)


def test_empty_cid_event_is_treated_as_keep_alive():
    """A real AX Hybrid PRO panel sends an empty cidEvent roughly every 2 seconds as its own
    keep-alive on the arming connection, instead of the generic eventType=heartBeat used by
    cameras/NVRs. This must not trigger a coordinator refresh on every single one -- that would
    hammer the panel with a status poll every ~2s indefinitely.
    """
    listener = SecurityArmingListener(MagicMock(), MagicMock(coordinators={}))

    listener._handle_event({"eventType": "cidEvent", "eventState": "inactive", "CIDEvent": {}})

    listener.hass.async_create_task.assert_not_called()
    listener.hass.bus.async_fire.assert_not_called()


def test_real_cid_event_triggers_refresh_and_bus_event():
    coordinator = MagicMock()
    device = MagicMock(coordinators={"security": coordinator})
    hass = MagicMock()
    # async_create_task isn't a real event loop here -- close the coroutine it's handed
    # instead of leaving it unawaited (avoids a "coroutine was never awaited" warning).
    hass.async_create_task.side_effect = lambda coro: coro.close()
    listener = SecurityArmingListener(hass, device)

    listener._handle_event(
        {
            "eventType": "cidEvent",
            "eventState": "active",
            "CIDEvent": {"type": "zoneAlarm", "code": 1130, "system": 1, "zone": 2},
        }
    )

    listener.hass.async_create_task.assert_called_once()
    listener.hass.bus.async_fire.assert_called_once()
