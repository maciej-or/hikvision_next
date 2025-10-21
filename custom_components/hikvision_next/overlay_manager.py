"""Overlay manager for Hikvision cameras."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

_LOGGER = logging.getLogger(__name__)

# Module-level storage for overlay tasks
# Key: (entry_id, camera_channel), Value: cancel callback function
_overlay_tasks: dict[tuple[str, int], Callable[[], None]] = {}


def _build_overlay_xml(
    overlay_id: int,
    enabled: bool,
    position_x: int,
    position_y: int,
    display_text: str,
) -> str:
    """Build ISAPI XML payload for text overlay."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<TextOverlayList version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
<TextOverlay>
<id>{overlay_id}</id>
<enabled>{str(enabled).lower()}</enabled>
<positionX>{position_x}</positionX>
<positionY>{position_y}</positionY>
<displayText>{display_text}</displayText>
</TextOverlay>
</TextOverlayList>"""


def _format_text(config: dict) -> str:
    """Format text based on mode (fixed or datetime)."""
    mode = config.get("mode", "fixed")
    text = config["text"]

    if mode == "fixed":
        return text
    elif mode == "datetime":
        # Use strftime to format current datetime
        return datetime.now().strftime(text)

    return text


async def async_update_overlay(hass: HomeAssistant, device, config: dict) -> None:
    """Update camera overlay via ISAPI."""
    camera_channel = config["camera_channel"]
    display_text = _format_text(config)

    xml_payload = _build_overlay_xml(
        overlay_id=1,
        enabled=config.get("enabled", True),
        position_x=config.get("position_x", 16),
        position_y=config.get("position_y", 570),
        display_text=display_text,
    )

    try:
        await device.request(
            "PUT",
            f"System/Video/inputs/channels/{camera_channel}/overlays/text/1",
            data=xml_payload,
        )
        _LOGGER.debug(
            "Updated overlay for camera %s: %s",
            camera_channel,
            display_text,
        )
    except ConnectionError as ex:
        _LOGGER.warning(
            "Camera %s is offline or unreachable, skipping overlay update: %s",
            camera_channel,
            ex,
        )
    except TimeoutError as ex:
        _LOGGER.warning(
            "Timeout updating overlay for camera %s: %s",
            camera_channel,
            ex,
        )
    except Exception as ex:
        _LOGGER.error(
            "Failed to update overlay for camera %s (type: %s): %s",
            camera_channel,
            type(ex).__name__,
            ex,
        )


async def async_start_overlay_updates(
    hass: HomeAssistant,
    entry_id: str,
    device,
    config: dict,
) -> None:
    """Start periodic overlay updates for a camera channel."""
    camera_channel = config["camera_channel"]
    interval_seconds = config.get("interval_seconds", 900)
    task_key = (entry_id, camera_channel)

    # Cancel existing task if any
    if task_key in _overlay_tasks:
        _LOGGER.debug(
            "Canceling existing overlay task for camera %s",
            camera_channel,
        )
        _overlay_tasks[task_key]()
        del _overlay_tasks[task_key]

    # Perform initial update
    await async_update_overlay(hass, device, config)

    # Schedule periodic updates
    cancel_callback = async_track_time_interval(
        hass,
        lambda _: hass.async_create_task(
            async_update_overlay(hass, device, config)
        ),
        timedelta(seconds=interval_seconds),
    )

    _overlay_tasks[task_key] = cancel_callback
    _LOGGER.info(
        "Started overlay updates for camera %s (interval: %ss)",
        camera_channel,
        interval_seconds,
    )


def async_stop_overlay_updates(entry_id: str, camera_channel: int) -> None:
    """Stop periodic overlay updates for a camera channel."""
    task_key = (entry_id, camera_channel)

    if task_key in _overlay_tasks:
        _overlay_tasks[task_key]()
        del _overlay_tasks[task_key]
        _LOGGER.info(
            "Stopped overlay updates for camera %s",
            camera_channel,
        )
