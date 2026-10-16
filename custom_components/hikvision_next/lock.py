"""Platform for ACS lock integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import ENTITY_ID_FORMAT, LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HikvisionConfigEntry
from .const import DOMAIN
from .door_control import LOCK_ALWAYS_OPEN_SUFFIX, DoorControlAction
from .hikvision_device import HikvisionDevice
from .isapi import EventInfo


def lock_group_key(unique_id: str) -> str:
    """Return the shared group key for sibling lock entities."""
    uid = unique_id.removeprefix("lock.") if unique_id.startswith("lock.") else unique_id
    return uid.removesuffix(LOCK_ALWAYS_OPEN_SUFFIX)


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
        for entity in (
            AcsMomentaryLockEntity(device, 0, event),
            AcsAlwaysOpenLockEntity(device, 0, event),
        ):
            if entity.unique_id in seen_unique_ids:
                continue
            seen_unique_ids.add(entity.unique_id)
            entities.append(entity)

    if entities:
        async_add_entities(entities)


class AcsLockEntity(LockEntity):
    """Base ACS door lock with shared state across sibling entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        device: HikvisionDevice,
        device_id: int,
        event: EventInfo,
        *,
        translation_key: str,
        unique_id_suffix: str = "",
    ) -> None:
        """Initialize."""
        unique_slug = f"{event.unique_id}{unique_id_suffix}"
        self.entity_id = ENTITY_ID_FORMAT.format(unique_slug)
        self._attr_unique_id = self.entity_id
        self._attr_translation_key = translation_key
        self._attr_device_info = device.hass_device_info(device_id)
        self._attr_entity_registry_enabled_default = not event.disabled
        self.device = device
        self.event = event
        self._lock_group_key = event.unique_id
        self._attr_is_locked = None

    async def async_added_to_hass(self) -> None:
        """Register for direct state updates from SDK/ISAPI event handlers."""
        await super().async_added_to_hass()
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        groups = domain_data.setdefault("event_lock_groups", {})
        groups.setdefault(self._lock_group_key, []).append(self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from direct state updates."""
        groups = self.hass.data.get(DOMAIN, {}).get("event_lock_groups", {})
        siblings = groups.get(self._lock_group_key, [])
        if self in siblings:
            siblings.remove(self)
        if not siblings:
            groups.pop(self._lock_group_key, None)
        await super().async_will_remove_from_hass()

    @property
    def is_locked(self) -> bool | None:
        """Return True if the lock is locked."""
        return self._attr_is_locked

    def set_locked(self, locked: bool) -> None:
        """Update lock state for this entity and its siblings."""
        groups = self.hass.data.get(DOMAIN, {}).get("event_lock_groups", {})
        entities = groups.get(self._lock_group_key, [self])
        for entity in entities:
            entity._attr_is_locked = locked
            if entity.platform is not None:
                entity.schedule_update_ha_state()

    async def _remote_control(self, action: DoorControlAction) -> None:
        try:
            await self.device.remote_control_door(self.event, action)
        except Exception as ex:
            raise HomeAssistantError(str(ex)) from ex


class AcsMomentaryLockEntity(AcsLockEntity):
    """Momentary ACS door lock (open/close pulse)."""

    def __init__(self, device: HikvisionDevice, device_id: int, event: EventInfo) -> None:
        super().__init__(device, device_id, event, translation_key="lock")

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the door (remote close)."""
        await self._remote_control(DoorControlAction.CLOSE)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door (remote open)."""
        await self._remote_control(DoorControlAction.OPEN)


class AcsAlwaysOpenLockEntity(AcsLockEntity):
    """ACS door lock with always-open / restore-normal control."""

    def __init__(self, device: HikvisionDevice, device_id: int, event: EventInfo) -> None:
        super().__init__(
            device,
            device_id,
            event,
            translation_key="lock_always_open",
            unique_id_suffix=LOCK_ALWAYS_OPEN_SUFFIX,
        )

    async def async_lock(self, **kwargs: Any) -> None:
        """Restore normal (close) door mode."""
        await self._remote_control(DoorControlAction.RESTORE_NORMAL)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Enable always-open door mode."""
        await self._remote_control(DoorControlAction.ALWAYS_OPEN)