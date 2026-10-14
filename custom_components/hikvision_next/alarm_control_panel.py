"""Platform for security control panel partitions (areas)."""

from __future__ import annotations

from homeassistant.components.alarm_control_panel import (
    ENTITY_ID_FORMAT,
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
)
from homeassistant.components.alarm_control_panel.const import AlarmControlPanelState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HikvisionConfigEntry
from .const import SECURITY_COORDINATOR
from .isapi import Partition
from .isapi.const import PARTITION_ARM_AWAY, PARTITION_ARM_STAY

PARTITION_ARMING_STATE = {
    PARTITION_ARM_AWAY: AlarmControlPanelState.ARMED_AWAY,
    PARTITION_ARM_STAY: AlarmControlPanelState.ARMED_HOME,
    "disarm": AlarmControlPanelState.DISARMED,
    "arming": AlarmControlPanelState.ARMING,
    "armFailed": AlarmControlPanelState.DISARMED,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add security control panel partitions."""

    device = entry.runtime_data
    coordinator = device.coordinators.get(SECURITY_COORDINATOR)

    entities = [PartitionAlarmPanel(partition, coordinator) for partition in device.partitions]

    async_add_entities(entities)


class PartitionAlarmPanel(CoordinatorEntity, AlarmControlPanelEntity):
    """Security control panel partition (area)."""

    _attr_has_entity_name = True
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_HOME | AlarmControlPanelEntityFeature.ARM_AWAY
    _attr_code_arm_required = False

    def __init__(self, partition: Partition, coordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_id = ENTITY_ID_FORMAT.format(partition.unique_id)
        self._attr_unique_id = self.entity_id
        self._attr_device_info = coordinator.device.hass_device_info()
        self._attr_name = partition.name
        self.partition = partition

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Return the state of the alarm panel."""
        data = self.coordinator.data.get(self.unique_id)
        if not data:
            return None
        if data.get("alarm"):
            return AlarmControlPanelState.TRIGGERED
        return PARTITION_ARMING_STATE.get(data.get("arming"))

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Send disarm command."""
        await self.coordinator.device.disarm_partition(self.partition.id)
        await self.coordinator.async_request_refresh()

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Send arm home (stay) command."""
        await self.coordinator.device.arm_partition(self.partition.id, PARTITION_ARM_STAY)
        await self.coordinator.async_request_refresh()

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Send arm away command."""
        await self.coordinator.device.arm_partition(self.partition.id, PARTITION_ARM_AWAY)
        await self.coordinator.async_request_refresh()
