"""Diagnostics support for Wiser"""
from __future__ import annotations

import inspect
import json
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DATA_ISAPI, DOMAIN, EVENTS_COORDINATOR

GET = "get"

ANON_KEYS = [
    "ip_address",
    "ip_addr",
    "mac_address",
    "macAddress",
    "serial_no",
    "serialNumber",
]


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return await _async_get_diagnostics(hass, entry)


@callback
async def _async_get_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: DeviceEntry | None = None,
) -> dict[str, Any]:
    isapi = hass.data[DOMAIN][entry.entry_id][DATA_ISAPI]
    coordinator = hass.data[DOMAIN][entry.entry_id][EVENTS_COORDINATOR]

    # Get info set
    info = {}

    # Add device info
    info.update({"Device Info": to_json(isapi.device_info)})

    # Add camera info
    info.update({"Cameras": [to_json(camera) for camera in isapi.cameras]})

    # Add event enabled states
    event_states = []
    for camera in isapi.cameras:
        for event in camera.supported_events:
            event_states.append(
                {
                    "camera": camera.id,
                    "event": event.id,
                    "enabled": await isapi.get_event_enabled_state(event),
                }
            )
    info.update({"Event States": event_states})

    # Event coordinator data
    info.update({"Entity Data": to_json(coordinator.data)})

    # Add raw device info
    info.update(await get_isapi_data("RAW Device Info", isapi.isapi.System.deviceInfo))

    # Add raw camera info - Direct connected
    info.update(await get_isapi_data("RAW Analog Camera Info", isapi.isapi.System.Video.inputs.channels))

    # Add raw camera info - Proxy connected
    info.update(await get_isapi_data("RAW IP Camera Info", isapi.isapi.ContentMgmt.InputProxy.channels))

    # Add raw capabilities
    info.update(await get_isapi_data("RAW Capabilities Info", isapi.isapi.System.capabilities))

    # Add raw supported events
    info.update(await get_isapi_data("RAW Events Info", isapi.isapi.Event.triggers))

    # Add raw streams info
    info.update(await get_isapi_data("RAW Streams Info", isapi.isapi.Streaming.channels))

    # Add raw holiday info
    info.update(await get_isapi_data("RAW Holiday Info", isapi.isapi.System.Holidays))

    # Add alarms server info
    info.update(await get_isapi_data("RAW Alarm Server Info", isapi.isapi.Event.notification.httpHosts))

    return info


async def get_isapi_data(title: str, path: object, filter_key: str = "") -> dict:
    """Get data from ISAPI."""
    try:
        response = await path(method=GET)
        if filter_key:
            response = response.get(filter_key, {})
        return {title: anonymise_data(response)}
    except Exception as ex:  # pylint: disable=broad-except
        return {title: ex}


def to_json(obj):
    """Convert object to json."""
    result = json.dumps(obj, cls=ObjectEncoder, sort_keys=True, indent=2)
    result = json.loads(result)
    result = anonymise_data(result)
    return result


def anonymise_data(data):
    """Anonymise sensitive data."""
    for key in ANON_KEYS:
        if data.get(key):
            data[key] = "**REDACTED**"
    return data


class ObjectEncoder(json.JSONEncoder):
    """Class to encode object to json."""

    def default(self, o):
        if hasattr(o, "to_json"):
            return self.default(o.to_json())

        if hasattr(o, "__dict__"):
            data = dict(
                (key, value)
                for key, value in inspect.getmembers(o)
                if not key.startswith("__")
                and not inspect.isabstract(value)
                and not inspect.isbuiltin(value)
                and not inspect.isfunction(value)
                and not inspect.isgenerator(value)
                and not inspect.isgeneratorfunction(value)
                and not inspect.ismethod(value)
                and not inspect.ismethoddescriptor(value)
                and not inspect.isroutine(value)
            )
            return self.default(data)
        return o
