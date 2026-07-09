"""ACS door remote control actions."""

from __future__ import annotations

from enum import StrEnum


class DoorControlAction(StrEnum):
    """Door lock control modes."""

    OPEN = "open"
    CLOSE = "close"
    ALWAYS_OPEN = "always_open"
    RESTORE_NORMAL = "restore_normal"


ISAPI_REMOTE_CONTROL_DOOR_CMD: dict[DoorControlAction, str] = {
    DoorControlAction.OPEN: "open",
    DoorControlAction.CLOSE: "close",
    DoorControlAction.ALWAYS_OPEN: "alwaysOpen",
    DoorControlAction.RESTORE_NORMAL: "close",
}

LOCK_ALWAYS_OPEN_SUFFIX = "_always_open"