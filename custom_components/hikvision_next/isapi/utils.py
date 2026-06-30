from functools import reduce
import json
from typing import Any

import xmltodict


def parse_isapi_response(response, present="dict"):
    """Parse Hikvision results."""
    if isinstance(response, (list,)):
        result = "".join(response)
    elif isinstance(response, str):
        result = response
    else:
        result = response.text

    if present is None or present == "dict":
        if isinstance(response, (list,)):
            events = []
            for event in response:
                e = json.loads(json.dumps(xmltodict.parse(event)))
                events.append(e)
            return events
        return json.loads(json.dumps(xmltodict.parse(result)))
    else:
        return result


def str_to_bool(value: str) -> bool:
    """Convert text to boolean."""
    if value:
        return value.lower() == "true"
    return False


def bool_to_str(value: bool) -> str:
    """Convert boolean to 'true' or 'false'."""
    return "true" if value else "false"


def get_stream_id(channel_id: str, stream_type: int = 1) -> int:
    """Get stream id."""
    return int(channel_id) * 100 + stream_type


from .const import CONNECTION_TYPE_PROXIED
from .models import AnalogCamera, IPCamera
from ..sdk.hcnetsdk import PAN_LEFT, PAN_RIGHT, TILT_DOWN, TILT_UP, ZOOM_IN, ZOOM_OUT

PTZ_ACTION_COMMANDS: dict[str, int] = {
    "ptz_up": TILT_UP,
    "ptz_down": TILT_DOWN,
    "ptz_left": PAN_LEFT,
    "ptz_right": PAN_RIGHT,
    "ptz_zoom_in": ZOOM_IN,
    "ptz_zoom_out": ZOOM_OUT,
}


def ptz_command_from_action(action: str) -> int | None:
    """Map a PTZ switch action id to an SDK PTZ command."""
    return PTZ_ACTION_COMMANDS.get(action)


def ptz_sdk_channel_candidates(camera: AnalogCamera | IPCamera, *, is_nvr: bool) -> list[int]:
    """Return SDK lChannel values to try for PTZ on a camera."""
    candidates = [camera.id]
    if is_nvr and isinstance(camera, IPCamera):
        if camera.input_port:
            candidates.append(camera.input_port + 32)
            if camera.input_port != camera.id:
                candidates.append(camera.input_port)
    return list(dict.fromkeys(candidates))


def ptz_prefers_isapi(camera: AnalogCamera | IPCamera, *, is_nvr: bool) -> bool:
    """NVR proxied IP cameras require ISAPI PTZCtrlProxy instead of SDK PTZ APIs."""
    return is_nvr and isinstance(camera, IPCamera) and camera.connection_type == CONNECTION_TYPE_PROXIED


_PTZ_CAP_METADATA_KEYS = frozenset({"@xmlns", "@version", "id", "#text"})


def ptz_channel_cap_indicates_support(ptz_cap: dict | None, *, proxied: bool) -> bool:
    """Return True when a PTZChannelCap payload exposes real PTZ control."""
    if not ptz_cap:
        return False

    for path in (
        "isSupportPTZ",
        "isSupportContinuous",
        "isSupportPatrols",
        "isSupportPreset",
        "ContinuousPanTiltZoom.isSupportContinuous",
        "ContinuousPanTiltZoom.enabled",
        "MomentaryPanTiltZoom.isSupportMomentary",
        "MomentaryPanTiltZoom.enabled",
    ):
        if str_to_bool(str(deep_get(ptz_cap, path, ""))):
            return True

    for key in ("maxPanPosition", "maxTiltPosition", "maxZoomRatio", "PTZLimits", "AbsoluteHigh"):
        if deep_get(ptz_cap, key) is not None:
            return True

    if not proxied:
        # Direct cameras only expose PTZCtrl on channels that support PTZ.
        return True

    # NVR PTZCtrlProxy responds on every channel; id-only payloads are not PTZ.
    return any(key not in _PTZ_CAP_METADATA_KEYS for key in ptz_cap)


def input_proxy_cap_indicates_ptz(capabilities: dict | None) -> bool:
    """Return True when InputProxy channel capabilities report PTZ support."""
    if not capabilities:
        return False
    channel_cap = deep_get(capabilities, "InputProxyChannelCap", capabilities)
    for key in ("isSupportPTZ", "isSupportPTZControl", "isSupportPtz"):
        if str_to_bool(str(deep_get(channel_cap, key, ""))):
            return True
    ptz_cap = deep_get(channel_cap, "PTZCap", deep_get(channel_cap, "PTZCtrlCap", {}))
    return ptz_channel_cap_indicates_support(ptz_cap, proxied=True)


def ptz_command_from_velocities(pan: int, tilt: int, zoom: int) -> int | None:
    """Derive an SDK PTZ command from ISAPI-style velocity values."""
    if zoom > 0:
        return ZOOM_IN
    if zoom < 0:
        return ZOOM_OUT
    if tilt > 0:
        return TILT_UP
    if tilt < 0:
        return TILT_DOWN
    if pan > 0:
        return PAN_RIGHT
    if pan < 0:
        return PAN_LEFT
    return None


def channel_from_bitmap(channels) -> int:
    """Return the first 1-based channel index set in an SDK channel bitmap."""
    if not channels:
        return 0
    for index, value in enumerate(channels):
        if value:
            return index + 1
    return 0


def deep_get(dictionary: dict, path: str, default: Any = None) -> Any:
    """Get safely nested dictionary attribute."""
    result = reduce(
        lambda d, key: d.get(key, default) if isinstance(d, dict) else default,
        path.split("."),
        dictionary,
    )
    if default == [] and not isinstance(result, list):
        return [result]

    return result
