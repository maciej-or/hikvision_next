"""Typed HCNetSDK preview bindings, independent of the alarm callbacks.

Layout follows HCNetSDK.h shipped with the Linux SDK, including 32-bit LONG
and DWORD and native pointer alignment on amd64/aarch64.
"""

import sys
from ctypes import CFUNCTYPE, POINTER, Structure, c_int, c_ubyte, c_void_p, sizeof

from .hcnetsdk import DWORD, LONG, NET_DVR_PREVIEWINFO, WORD

SYSHEAD = 1
STREAMDATA = 2


class PacketInfo(Structure):
    """NET_DVR_PACKET_INFO_EX."""

    _fields_ = [
        ("wWidth", WORD),
        ("wHeight", WORD),
        *[
            (name, DWORD)
            for name in (
                "dwTimeStamp",
                "dwTimeStampHigh",
                "dwYear",
                "dwMonth",
                "dwDay",
                "dwHour",
                "dwMinute",
                "dwSecond",
                "dwMillisecond",
                "dwFrameNum",
                "dwFrameRate",
                "dwFlag",
                "dwFilePos",
                "dwPacketType",
                "dwPacketSize",
            )
        ],
        ("pPacketBuffer", c_void_p),
        ("byRes1", c_ubyte * 4),
        ("dwPacketMode", DWORD),
        ("byRes2", c_ubyte * 16),
        ("dwReserved", DWORD * 6),
    ]


RealDataCallback = CFUNCTYPE(None, LONG, DWORD, c_void_p, DWORD, c_void_p)
ESCallback = CFUNCTYPE(None, LONG, POINTER(PacketInfo), c_void_p)


def preview_channel(device, stream) -> int:
    """Map ISAPI's logical NVR channel to the SDK's digital-channel range.

    For example a pure NVR's /Streaming/channels/102 is usually SDK channel
    33, sub-stream 1. Hybrid recorders number logical digital inputs after
    their analog inputs. Never use srcInputPort: it belongs to the *remote*
    camera and is commonly 1 for every input on a recorder.
    """
    from ..isapi.models import IPCamera

    channel = int(stream.id) // 100
    info = getattr(device, "sdk_device_info", None)
    if info is not None and device.device_info.is_nvr:
        for camera in device.cameras:
            if camera.id == channel and isinstance(camera, IPCamera):
                start = int(info.byStartDChan) & 0xFF
                if start:
                    return start + channel - device.capabilities.analog_cameras_inputs - 1
    return channel


def bind_preview(sdk):
    """Bind preview functions without changing the existing alarm ABI."""
    if (
        sys.platform != "linux"
        or sizeof(c_void_p) != 8
        or sizeof(NET_DVR_PREVIEWINFO) != 280
        or sizeof(PacketInfo) != 120
    ):
        raise RuntimeError("Unsupported HCNetSDK preview structure layout")
    for name, args, result in (
        ("NET_DVR_RealPlay_V40", [LONG, POINTER(NET_DVR_PREVIEWINFO), RealDataCallback, c_void_p], LONG),
        ("NET_DVR_StopRealPlay", [LONG], c_int),
        ("NET_DVR_SetESRealPlayCallBack", [LONG, ESCallback, c_void_p], c_int),
        ("NET_DVR_MakeKeyFrame", [LONG, LONG], c_int),
        ("NET_DVR_MakeKeyFrameSub", [LONG, LONG], c_int),
    ):
        if func := getattr(sdk, name, None):
            func.argtypes = args
            func.restype = result
