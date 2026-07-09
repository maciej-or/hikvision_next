"""Platform for ACS lock integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import ENTITY_ID_FORMAT, LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HikvisionConfigEntry
from .const import DOMAIN
from .hikvision_device import HikvisionDevice
from .isapi import EventInfo


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add ACS lock entities for door stations."""
    device = entry.runtime_data

    entities = []
    seen_unique_ids: set[str] = set()

    for event in device.events_info:
        if event.id != "lock" or not event.url:
            continue
        entity = AcsLockEntity(device, 0, event)
        if entity.unique_id in seen_unique_ids:
            continue
        seen_unique_ids.add(entity.unique_id)
        entities.append(entity)

    if entities:
        async_add_entities(entities)


class AcsLockEntity(LockEntity):
    """ACS door lock controlled via ISAPI with state from SDK feedback."""

    _attr_has_entity_name = True

    def __init__(self, device: HikvisionDevice, device_id: int, event: EventInfo) -> None:
        """Initialize."""
        self.entity_id = ENTITY_ID_FORMAT.format(event.unique_id)
        self._attr_unique_id = self.entity_id
        self._attr_translation_key = "lock"
        self._attr_device_info = device.hass_device_info(device_id)
        self._attr_entity_registry_enabled_default = not event.disabled
        self.device = device
        self.event = event
        self._attr_is_locked = None

    async def async_added_to_hass(self) -> None:
        """Register for direct state updates from SDK/ISAPI event handlers."""
        await super().async_added_to_hass()
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        locks = domain_data.setdefault("event_lock_entities", {})
        locks[self.unique_id] = self

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from direct state updates."""
        locks = self.hass.data.get(DOMAIN, {}).get("event_lock_entities", {})
        locks.pop(self.unique_id, None)
        await super().async_will_remove_from_hass()

    @property
    def is_locked(self) -> bool | None:
        """Return True if the lock is locked."""
        return self._attr_is_locked

    def set_locked(self, locked: bool) -> None:
        """Update lock state from SDK ACS lock open/close feedback."""
        self._attr_is_locked = locked
        if self.platform is not None:
            self.schedule_update_ha_state()

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the door (remote close)."""
        try:
            await self.device.remote_control_door(self.event, locked=True)
        except Exception as ex:
            raise HomeAssistantError(str(ex)) from ex

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door (remote open)."""
        try:
            await self.device.remote_control_door(self.event, locked=False)
        except Exception as ex:
            raise HomeAssistantError(str(ex)) from ex