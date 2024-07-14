"""Diagnostics support for Wiser."""

from __future__ import annotations

import inspect
import json
import random
from typing import Any

from httpx import HTTPStatusError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DATA_ISAPI, DOMAIN, STREAM_TYPE

GET = "get"


def anonymise_mac(orignal: str):
    """Anonymise MAC address."""

    mac = [random.randint(0x00, 0xFF) for _ in range(6)]
    return ":".join("%02x" % x for x in mac)


def anonymise_ip(orignal: str):
    """Anonymise IP address."""
    if not orignal or orignal[0] == "0":
        return orignal
    return f"1.0.0.{random.randint(0x00, 0xff)}"


def anonymise_serial(orignal: str):
    """Anonymise serial number."""

    if len(orignal) > 32:
        return orignal[:12] + "".join("0" if c.isdigit() else c for c in orignal[12:])
    return "".join("0" if c.isdigit() else c for c in orignal)


ANON_KEYS = {
    "ip_address": anonymise_ip,
    "ip_addr": anonymise_ip,
    "ipAddress": anonymise_ip,
    "mac_address": anonymise_mac,
    "macAddress": anonymise_mac,
    "serial_no": anonymise_serial,
    "serialNumber": anonymise_serial,
    "subSerialNumber": anonymise_serial,
    "unique_id": anonymise_serial,
    "deviceID": anonymise_serial,
}

anon_map = {}


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

    # Get info set
    info = {}

    # Add camera info
    # info.update({"Cameras": [to_json(camera) for camera in isapi.cameras]})

    # ISAPI responses
    responses = {}
    endpoints = [
        "System/deviceInfo",
        "System/capabilities",
        "System/IO/inputs/1/status",
        "System/IO/outputs/1/status",
        "System/Holidays",
        "System/Video/inputs/channels",
        "ContentMgmt/InputProxy/channels",
        "ContentMgmt/Storage",
        "Security/adminAccesses",
        "Event/triggers",
        "Event/triggers/scenechangedetection-1",
        "Event/notification/httpHosts",
    ]

    for endpoint in endpoints:
        responses[endpoint] = await get_isapi_data(isapi, endpoint)

    # channels
    for camera in isapi.cameras:
        for stream_type_id in STREAM_TYPE:
            endpoint = f"Streaming/channels/{camera.id}0{stream_type_id}"
            responses[endpoint] = await get_isapi_data(isapi, endpoint)

    # event states
    for camera in isapi.cameras:
        for event in camera.events_info:
            responses[event.url] = await get_isapi_data(isapi, event.url)

    info["ISAPI"] = responses
    return info


async def get_isapi_data(isapi, endpoint: str) -> dict:
    """Get data from ISAPI."""
    entry = {}
    try:
        response = await isapi.request(GET, endpoint)
        entry["response"] = anonymise_data(response)
    except HTTPStatusError as ex:
        entry["status_code"] = ex.response.status_code
    except Exception as ex:
        entry["error"] = ex
    return entry


def to_json(obj):
    """Convert object to json."""
    result = json.dumps(obj, cls=ObjectEncoder, sort_keys=True, indent=2)
    result = json.loads(result)
    return anonymise_data(result)


def anonymise_data(data):
    """Anonymise sensitive data."""
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if key in ANON_KEYS and value is not None:
                if value in anon_map:
                    result[key] = anon_map[value]
                else:
                    anon_fn = ANON_KEYS[key]
                    anon_map[value] = result[key] = anon_fn(value)
            else:
                result[key] = anonymise_data(value)
        return result
    if isinstance(data, list):
        result = []
        for item in data:
            result.append(anonymise_data(item))
        return result
    return data


class ObjectEncoder(json.JSONEncoder):
    """Class to encode object to json."""

    def default(self, o):
        """Implement encoding logic."""
        if hasattr(o, "to_json"):
            return self.default(o.to_json())

        if hasattr(o, "__dict__"):
            data = {
                key: value
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
            }
            return self.default(data)
        return o
