"""Tests for video intercom control buttons."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.hikvision_next.button import (
    IntercomAnswerButton,
    IntercomCallButton,
    IntercomHangupButton,
    IntercomRejectButton,
)
from custom_components.hikvision_next.coordinator import IntercomStatusCoordinator
from custom_components.hikvision_next.sdk.video_intercom import IntercomCallState


@pytest.fixture
def intercom_coordinator(hass: HomeAssistant) -> IntercomStatusCoordinator:
    device = MagicMock()
    device.device_info.serial_no = "DS-K1T6QT TEST"
    device.hass_device_info.return_value = {}
    device.intercom_answer = AsyncMock()
    device.intercom_call = AsyncMock()
    device.intercom_reject = AsyncMock()
    device.intercom_hangup = AsyncMock()
    coordinator = IntercomStatusCoordinator(hass, device)
    return coordinator


async def test_call_button_available_only_when_idle(
    hass: HomeAssistant,
    intercom_coordinator: IntercomStatusCoordinator,
) -> None:
    button = IntercomCallButton(intercom_coordinator, "ds_k1t6qt_test")
    button.hass = hass

    assert button.available is False

    await intercom_coordinator.async_set_connected(True)
    assert button.available is True

    await intercom_coordinator.async_set_call_state(IntercomCallState.RINGING)
    assert button.available is False


async def test_call_button_presses_device_action(
    hass: HomeAssistant,
    intercom_coordinator: IntercomStatusCoordinator,
) -> None:
    button = IntercomCallButton(intercom_coordinator, "ds_k1t6qt_test")
    button.hass = hass

    await intercom_coordinator.async_set_connected(True)

    await button.async_press()
    intercom_coordinator.device.intercom_call.assert_awaited_once()


async def test_answer_button_available_only_when_ringing(
    hass: HomeAssistant,
    intercom_coordinator: IntercomStatusCoordinator,
) -> None:
    button = IntercomAnswerButton(intercom_coordinator, "ds_k1t6qt_test")
    button.hass = hass

    await intercom_coordinator.async_set_connected(True)
    assert button.available is False

    await intercom_coordinator.async_set_call_state(IntercomCallState.RINGING)
    assert button.available is True

    await intercom_coordinator.async_set_call_state(IntercomCallState.IN_CALL)
    assert button.available is False


async def test_hangup_button_available_only_when_in_call(
    hass: HomeAssistant,
    intercom_coordinator: IntercomStatusCoordinator,
) -> None:
    button = IntercomHangupButton(intercom_coordinator, "ds_k1t6qt_test")
    button.hass = hass

    await intercom_coordinator.async_set_connected(True)
    assert button.available is False

    await intercom_coordinator.async_set_call_state(IntercomCallState.IN_CALL)
    assert button.available is True


async def test_reject_button_presses_device_action(
    hass: HomeAssistant,
    intercom_coordinator: IntercomStatusCoordinator,
) -> None:
    button = IntercomRejectButton(intercom_coordinator, "ds_k1t6qt_test")
    button.hass = hass

    await intercom_coordinator.async_set_connected(True)
    await intercom_coordinator.async_set_call_state(IntercomCallState.RINGING)

    await button.async_press()
    intercom_coordinator.device.intercom_reject.assert_awaited_once()


async def test_hangup_button_presses_device_action(
    hass: HomeAssistant,
    intercom_coordinator: IntercomStatusCoordinator,
) -> None:
    button = IntercomHangupButton(intercom_coordinator, "ds_k1t6qt_test")
    button.hass = hass

    await intercom_coordinator.async_set_connected(True)
    await intercom_coordinator.async_set_call_state(IntercomCallState.IN_CALL)

    await button.async_press()
    intercom_coordinator.device.intercom_hangup.assert_awaited_once()