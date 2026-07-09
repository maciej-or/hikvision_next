"""Platform for video intercom control buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from . import HikvisionConfigEntry
from .const import CONF_CONNECTION_SDK
from .coordinator import IntercomStatusCoordinator
from .hikvision_device import HikvisionDevice
from .sdk.utils import SDKError
from .sdk.video_intercom import IntercomCallState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add intercom control buttons for video intercom devices."""
    device = entry.runtime_data
    if (
        device.connection_type != CONF_CONNECTION_SDK
        or not device.capabilities.support_video_intercom
        or device.intercom_coordinator is None
    ):
        return

    coordinator = device.intercom_coordinator
    serial = slugify(device.device_info.serial_no.lower())
    async_add_entities(
        [
            IntercomAnswerButton(coordinator, serial),
            IntercomRejectButton(coordinator, serial),
            IntercomHangupButton(coordinator, serial),
        ]
    )


class IntercomControlButton(CoordinatorEntity, ButtonEntity):
    """Base button for video intercom call control."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: IntercomStatusCoordinator,
        serial: str,
        *,
        translation_key: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{serial}_{translation_key}"
        self._attr_translation_key = translation_key
        self._attr_icon = icon
        self._attr_device_info = coordinator.device.hass_device_info(0)

    @property
    def device(self) -> HikvisionDevice:
        return self.coordinator.device

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.connected


class IntercomAnswerButton(IntercomControlButton):
    """Answer an incoming video intercom call."""

    def __init__(self, coordinator: IntercomStatusCoordinator, serial: str) -> None:
        super().__init__(
            coordinator,
            serial,
            translation_key="intercom_answer",
            icon="mdi:phone-check",
        )

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.call_state == IntercomCallState.RINGING
        )

    async def async_press(self) -> None:
        try:
            await self.device.intercom_answer()
        except (ValueError, SDKError) as ex:
            raise HomeAssistantError(str(ex)) from ex


class IntercomRejectButton(IntercomControlButton):
    """Reject an incoming video intercom call."""

    def __init__(self, coordinator: IntercomStatusCoordinator, serial: str) -> None:
        super().__init__(
            coordinator,
            serial,
            translation_key="intercom_reject",
            icon="mdi:phone-cancel",
        )

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.call_state == IntercomCallState.RINGING
        )

    async def async_press(self) -> None:
        try:
            await self.device.intercom_reject()
        except (ValueError, SDKError) as ex:
            raise HomeAssistantError(str(ex)) from ex


class IntercomHangupButton(IntercomControlButton):
    """End the active video intercom call."""

    def __init__(self, coordinator: IntercomStatusCoordinator, serial: str) -> None:
        super().__init__(
            coordinator,
            serial,
            translation_key="intercom_hangup",
            icon="mdi:phone-hangup",
        )

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.call_state == IntercomCallState.IN_CALL
        )

    async def async_press(self) -> None:
        try:
            await self.device.intercom_hangup()
        except (ValueError, SDKError) as ex:
            raise HomeAssistantError(str(ex)) from ex