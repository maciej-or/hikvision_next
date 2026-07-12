"""ACS door lock control via SDK."""

from __future__ import annotations

import logging
import re
from ctypes import CDLL, byref, sizeof

from ..door_control import DoorControlAction
from .hcnetsdk import (
    GatewayCommand,
    LONG,
    NET_DVR_CONTROL_GATEWAY,
    NET_DVR_REMOTECONTROL_GATEWAY,
)
from .utils import SDKError

_LOGGER = logging.getLogger(__name__)

_DOOR_URL_RE = re.compile(r"/door/(\d+)", re.IGNORECASE)

_SDK_GATEWAY_COMMAND: dict[DoorControlAction, GatewayCommand] = {
    DoorControlAction.CLOSE: GatewayCommand.CLOSE,
    DoorControlAction.OPEN: GatewayCommand.OPEN,
    DoorControlAction.ALWAYS_OPEN: GatewayCommand.NORMALLY_OPEN,
    DoorControlAction.RESTORE_NORMAL: GatewayCommand.RESTORE_NORMAL,
}


def door_index_from_event_url(url: str | None) -> int:
    """Parse 1-based door index from an ISAPI RemoteControl door URL."""
    if not url:
        return 1
    match = _DOOR_URL_RE.search(url)
    return int(match.group(1)) if match else 1


def sdk_remote_control_door(
    sdk: CDLL,
    user_id: LONG,
    door_index: int,
    action: DoorControlAction,
) -> None:
    """Control an ACS door via NET_DVR_RemoteControl."""
    gateway = NET_DVR_CONTROL_GATEWAY()
    gateway.dwSize = sizeof(NET_DVR_CONTROL_GATEWAY)
    gateway.dwGatewayIndex = door_index
    gateway.byCommand = int(_SDK_GATEWAY_COMMAND[action])
    gateway.byLockType = 0
    gateway.wLockID = 0
    gateway.byControlType = 2

    result = sdk.NET_DVR_RemoteControl(
        user_id,
        NET_DVR_REMOTECONTROL_GATEWAY,
        byref(gateway),
        sizeof(gateway),
    )
    if not result:
        raise SDKError(
            sdk,
            f"NET_DVR_RemoteControl door {door_index} {action} failed",
        )
    _LOGGER.info("SDK remote door control door=%s action=%s", door_index, action)