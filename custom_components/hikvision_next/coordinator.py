"""Coordinators."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
import logging

from homeassistant.components.alarm_control_panel import ENTITY_ID_FORMAT as ALARM_ENTITY_ID_FORMAT
from homeassistant.components.binary_sensor import ENTITY_ID_FORMAT as BINARY_SENSOR_ENTITY_ID_FORMAT
from homeassistant.components.switch import ENTITY_ID_FORMAT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import slugify

from .const import (
    CONF_ALARM_SERVER_HOST,
    DOMAIN,
    HIKVISION_EVENT,
    HOLIDAY_MODE,
    SECURITY_COORDINATOR,
    SECURITY_HOST_STATUS,
)
from .isapi.const import ZONE_CATEGORY_TAMPER, ZONE_DETECTOR_CATEGORY

SCAN_INTERVAL_EVENTS = timedelta(seconds=120)
SCAN_INTERVAL_HOLIDAYS = timedelta(minutes=60)
SCAN_INTERVAL_SECURITY = timedelta(seconds=30)

ARMING_RECONNECT_DELAY = 5
ARMING_MAX_RECONNECT_DELAY = 60
# A cidEvent push arrives close to instantly, but the panel's own SecurityCP/status
# computation lags a little behind it -- polling immediately on receipt can still return
# stale data. A short follow-up refresh catches the update without waiting for the next
# full 30s poll cycle.
ARMING_EVENT_FOLLOWUP_DELAY = 3

_LOGGER = logging.getLogger(__name__)


def _resolve_update_interval(interval_override: int | None, default: timedelta) -> timedelta | None:
    """Resolve a coordinator's poll interval from the user-configured override, if any.

    interval_override comes from the config entry's "polling_interval" option: unset (None)
    keeps the device type's own default, 0 disables automatic polling entirely (None
    update_interval -- DataUpdateCoordinator then only refreshes on explicit/push-triggered
    requests), and any positive value overrides the default interval.
    """
    if interval_override is None:
        return default
    if interval_override == 0:
        return None
    return timedelta(seconds=interval_override)


class EventsCoordinator(DataUpdateCoordinator):
    """Manage fetching events state from NVR or camera."""

    def __init__(self, hass: HomeAssistant, device, interval_override: int | None = None) -> None:
        """Initialize."""
        self.device = device

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=_resolve_update_interval(interval_override, SCAN_INTERVAL_EVENTS),
        )

    async def _async_update_data(self):
        """Update data via ISAPI."""
        data = {}

        # Get camera event status
        for camera in self.device.cameras:
            for event in camera.events_info:
                if event.disabled:
                    continue
                try:
                    _id = ENTITY_ID_FORMAT.format(event.unique_id)
                    data[_id] = await self.device.get_event_enabled_state(event)
                except Exception as ex:  # pylint: disable=broad-except
                    self.device.handle_exception(ex, f"Cannot fetch state for {event.id}")

        # Get NVR event status
        for event in self.device.events_info:
            if event.disabled:
                continue
            try:
                _id = ENTITY_ID_FORMAT.format(event.unique_id)
                data[_id] = await self.device.get_event_enabled_state(event)
            except Exception as ex:  # pylint: disable=broad-except
                self.device.handle_exception(ex, f"Cannot fetch state for {event.id}")

        # Get output port(s) status
        for i in range(1, self.device.capabilities.output_ports + 1):
            try:
                _id = ENTITY_ID_FORMAT.format(f"{slugify(self.device.device_info.serial_no.lower())}_{i}_alarm_output")
                data[_id] = await self.device.get_io_port_status("output", i)
            except Exception as ex:  # pylint: disable=broad-except
                self.device.handle_exception(ex, f"Cannot fetch state for alarm output {i}")

        # Refresh HDD data
        try:
            self.device.storage = await self.device.get_storage_devices()
        except Exception as ex:  # pylint: disable=broad-except
            self.device.handle_exception(ex, "Cannot fetch storage state")


        if self.device.auth_token_expired:
            self.device.auth_token_expired = False

        return data


class SecondaryCoordinator(DataUpdateCoordinator):
    """Manage fetching events state from NVR."""

    def __init__(self, hass: HomeAssistant, device) -> None:
        """Initialize."""
        self.device = device

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL_HOLIDAYS,
        )

    async def _async_update_data(self):
        """Update data via ISAPI."""
        data = {}
        try:
            if self.device.capabilities.support_holiday_mode:
                data[HOLIDAY_MODE] = await self.device.get_holiday_enabled_state()
        except Exception as ex:  # pylint: disable=broad-except
            self.device.handle_exception(ex, f"Cannot fetch state for {HOLIDAY_MODE}")
        try:
            if self.device.capabilities.support_alarm_server:
                alarm_server = await self.device.get_alarm_server()
                data[CONF_ALARM_SERVER_HOST] = {
                    "protocol_type": alarm_server.protocol_type,
                    "address": alarm_server.ip_address or alarm_server.host_name,
                    "port_no": alarm_server.port_no,
                    "path": alarm_server.url,

                }
        except Exception as ex:  # pylint: disable=broad-except
            self.device.handle_exception(ex, f"Cannot fetch state for {CONF_ALARM_SERVER_HOST}")
        return data


class SecurityCoordinator(DataUpdateCoordinator):
    """Manage fetching partition/zone status from a security control panel."""

    def __init__(self, hass: HomeAssistant, device, interval_override: int | None = None) -> None:
        """Initialize."""
        self.device = device

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=_resolve_update_interval(interval_override, SCAN_INTERVAL_SECURITY),
        )

    async def _async_update_data(self):
        """Update data via ISAPI."""
        data = {}

        try:
            for status in await self.device.get_partitions_status():
                partition = self.device.get_partition_by_id(status.id)
                if not partition:
                    continue
                _id = ALARM_ENTITY_ID_FORMAT.format(partition.unique_id)
                data[_id] = {"arming": status.arming, "alarm": status.alarm}
        except Exception as ex:  # pylint: disable=broad-except
            self.device.handle_exception(ex, "Cannot fetch partition status")

        try:
            for status in await self.device.get_zones_status():
                zone = self.device.get_zone_by_id(status.id)
                if not zone:
                    continue
                _id = BINARY_SENSOR_ENTITY_ID_FORMAT.format(zone.unique_id)
                data[_id] = {
                    "triggered": _zone_is_triggered(status),
                    "status": status.status,
                    "bypassed": status.bypassed,
                    "tamper_evident": status.tamper_evident,
                    "charge": status.charge,
                    "magnet_open_status": status.magnet_open_status,
                    "charge_value": status.charge_value,
                    "signal": status.signal,
                    "temperature": status.temperature,
                    "humidity": status.humidity,
                    "alarm": status.alarm,
                    "armed": status.armed,
                    "is_via_repeater": status.is_via_repeater,
                    "stay_away": status.stay_away,
                }
        except Exception as ex:  # pylint: disable=broad-except
            self.device.handle_exception(ex, "Cannot fetch zone status")

        try:
            for status in await self.device.get_peripherals():
                peripheral = self.device.get_peripheral_by_kind_and_id(status.kind, status.id)
                if not peripheral:
                    continue
                data[peripheral.unique_id] = {
                    "status": status.status,
                    "tamper_evident": status.tamper_evident,
                    "charge": status.charge,
                    "charge_value": status.charge_value,
                    "signal": status.signal,
                    "temperature": status.temperature,
                    "mains_power": status.mains_power,
                }
        except Exception as ex:  # pylint: disable=broad-except
            self.device.handle_exception(ex, "Cannot fetch peripheral status")

        try:
            host_status = await self.device.get_host_status()
            data[SECURITY_HOST_STATUS] = {
                "tamper_evident": host_status.tamper_evident,
                "ac_connected": host_status.ac_connected,
                "fault_count": host_status.fault_count,
                "battery_status": host_status.battery_status,
                "battery_percent": host_status.battery_percent,
                "battery_voltage": host_status.battery_voltage,
            }
        except Exception as ex:  # pylint: disable=broad-except
            self.device.handle_exception(ex, "Cannot fetch security host status")

        return data


def _zone_is_triggered(zone) -> bool:
    """Resolve whether a zone should be reported as "on" (triggered/open).

    "status" == "trigger" is ISAPI's own authoritative "this zone is currently active"
    signal. Confirmed against two different real magnetic contact zones on an AX Hybrid PRO
    panel over an extended period: "status" reliably flipped between "online" (closed) and
    "trigger" (open) in step with the physical contact, while "magnetOpenStatus" was
    unreliable on both -- one zone reported it permanently stuck at True, the other
    permanently stuck at False, regardless of the real door position. "alarm" isn't used
    either: it reflects an armed-system alarm condition, which stays false while the
    partition is disarmed even if the detector itself is actively triggered, so it can't
    drive a sensor that's meant to reflect real-time physical state.

    Tamper-only zones (detectorType "tamperDetector") are the exception: confirmed against
    real hardware that "status" stays "online" regardless of tamper state -- "tamperEvident"
    is what actually flips True/False there, so that's used instead for that category.
    """
    if ZONE_DETECTOR_CATEGORY.get(zone.detector_type) == ZONE_CATEGORY_TAMPER:
        return zone.tamper_evident
    return zone.status == "trigger"


class SecurityArmingListener:
    """Keeps a persistent ISAPI "arming" connection open for real-time panel events.

    SecurityCP panels don't deliver their own zone/partition/CID events via the generic
    listening-mode mechanism (Event/notification/httpHosts) used by cameras/NVRs -- that
    was confirmed against real hardware to never fire for these events, even when the
    listening host is correctly configured. Real-time delivery instead requires "arming with
    subscription": holding a POST Event/notification/subscribeEvent connection open
    indefinitely and reacting to events as the device pushes them down that same connection
    (see ISAPIClient.open_arming_stream).
    """

    def __init__(self, hass: HomeAssistant, device) -> None:
        """Initialize."""
        self.hass = hass
        self.device = device
        self._task: asyncio.Task | None = None
        self._stopped = False

    def async_start(self) -> None:
        """Start the background listener task."""
        self._stopped = False
        self._task = self.hass.async_create_background_task(
            self._async_run(), name=f"hikvision_next arming listener {self.device.host}"
        )

    async def async_stop(self) -> None:
        """Stop the background listener task."""
        self._stopped = True
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _async_run(self) -> None:
        """Maintain the arming connection, reconnecting with backoff on failure."""
        delay = ARMING_RECONNECT_DELAY
        while not self._stopped:
            try:
                _LOGGER.info("Opening arming connection to %s for real-time panel events", self.device.host)
                async for event in self.device.open_arming_stream():
                    delay = ARMING_RECONNECT_DELAY
                    self._handle_event(event)
            except asyncio.CancelledError:
                raise
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.warning(
                    "Arming connection to %s lost (%s); reconnecting in %ss", self.device.host, ex, delay
                )

            if self._stopped:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, ARMING_MAX_RECONNECT_DELAY)

    def _handle_event(self, event: dict) -> None:
        """Handle a single parsed event from the arming connection."""
        event_type = event.get("eventType")
        if event_type == "heartBeat":
            _LOGGER.debug("Arming connection heartbeat from %s", self.device.host)
            return

        cid_event = event.get("CIDEvent", {})
        if event_type == "cidEvent" and not cid_event:
            # This panel emits its own keep-alive as an empty cidEvent (eventState=inactive,
            # no CIDEvent details) roughly every 2 seconds, rather than the generic
            # eventType=heartBeat used by cameras/NVRs. Refreshing on every one of these would
            # hammer the panel with a status poll every ~2s for no reason.
            _LOGGER.debug("Arming connection keep-alive (empty cidEvent) from %s", self.device.host)
            return

        _LOGGER.info(
            "Security control panel event from %s (eventType=%s, eventState=%s, CIDEvent=%s)",
            self.device.host,
            event_type,
            event.get("eventState"),
            cid_event,
        )

        coordinator = self.device.coordinators.get(SECURITY_COORDINATOR)
        if coordinator:
            self.hass.async_create_task(self._async_refresh_with_followup(coordinator))

        self.hass.bus.async_fire(
            HIKVISION_EVENT,
            {
                "event_type": event_type,
                "event_state": event.get("eventState"),
                "cid_type": cid_event.get("type"),
                "cid_code": cid_event.get("code"),
                "partition": cid_event.get("system"),
                "zone": cid_event.get("zone"),
                "zone_name": cid_event.get("zoneName"),
            },
        )

    @staticmethod
    async def _async_refresh_with_followup(coordinator) -> None:
        """Refresh immediately, then again after a short delay.

        Confirmed against real hardware: a cidEvent push arrives almost instantly, but
        polling SecurityCP/status right away can still return the pre-change value -- the
        panel's own status computation hasn't caught up yet. The follow-up refresh catches
        the update a few seconds later instead of waiting for the next full poll cycle.
        """
        await coordinator.async_request_refresh()
        await asyncio.sleep(ARMING_EVENT_FOLLOWUP_DELAY)
        await coordinator.async_request_refresh()
