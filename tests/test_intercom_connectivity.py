"""Tests for split event vs intercom connectivity coordinators."""

from ctypes import POINTER, addressof, cast, create_string_buffer, sizeof

from custom_components.hikvision_next.coordinator import (
    IntercomStatusCoordinator,
    SubscribeStatusCoordinator,
)
from custom_components.hikvision_next.sdk.hcnetsdk import (
    DWORD,
    NET_SDK_CALLBACK_STATUS_EXCEPTION,
    NET_SDK_CALLBACK_STATUS_FAILED,
    NET_SDK_CALLBACK_STATUS_SUCCESS,
    NET_SDK_CALLBACK_TYPE_STATUS,
    NET_SDK_CONFIG_STATUS_EXCEPTION,
    NET_SDK_CONFIG_STATUS_FAILED,
)
from custom_components.hikvision_next.sdk.video_intercom import VideoIntercomRemoteConfig


class FakeDevice:
    device_info = type("Info", (), {"serial_no": "DS-KD8003-E6"})()


def _status_buffer(status: int, error_code: int | None = None):
    if error_code is None:
        buf = create_string_buffer(sizeof(DWORD))
        cast(buf, POINTER(DWORD)).contents.value = status
        return buf
    buf = create_string_buffer(8)
    cast(buf, POINTER(DWORD)).contents.value = status
    cast(addressof(buf) + 4, POINTER(DWORD)).contents.value = error_code
    return buf


def test_subscribe_and_intercom_coordinators_are_independent():
    device = FakeDevice()
    subscribe = SubscribeStatusCoordinator(None, device)
    intercom = IntercomStatusCoordinator(None, device)

    assert subscribe.connected is False
    assert intercom.connected is False

    subscribe._connected = True
    assert subscribe.connected is True
    assert intercom.connected is False


def test_video_intercom_remote_config_handles_callback_status_success():
    events = []
    statuses = []

    class FakeSdk:
        def NET_DVR_StartRemoteConfig(self, *_args, **_kwargs):
            return 1

        def NET_DVR_StopRemoteConfig(self, _handle):
            return True

        def NET_DVR_GetLastError(self):
            return 0

    session = VideoIntercomRemoteConfig(
        FakeSdk(),
        1,
        on_event=events.append,
        on_status_change=lambda connected, reason=None: statuses.append((connected, reason)),
    )
    session._handle = 1
    buf = _status_buffer(NET_SDK_CALLBACK_STATUS_SUCCESS)
    session._handle_callback(NET_SDK_CALLBACK_TYPE_STATUS, buf, sizeof(DWORD))

    assert statuses == [(True, None)]


def test_video_intercom_remote_config_handles_callback_status_failed_with_error_code():
    events = []
    statuses = []

    class FakeSdk:
        def NET_DVR_StartRemoteConfig(self, *_args, **_kwargs):
            return 1

        def NET_DVR_StopRemoteConfig(self, _handle):
            return True

        def NET_DVR_GetLastError(self):
            return 0

    session = VideoIntercomRemoteConfig(
        FakeSdk(),
        1,
        on_event=events.append,
        on_status_change=lambda connected, reason=None: statuses.append((connected, reason)),
    )
    session._handle = 1
    session._handle_callback(
        NET_SDK_CALLBACK_TYPE_STATUS,
        _status_buffer(NET_SDK_CALLBACK_STATUS_FAILED, 9),
        8,
    )
    session._handle_callback(
        NET_SDK_CALLBACK_TYPE_STATUS,
        _status_buffer(NET_SDK_CALLBACK_STATUS_EXCEPTION),
        sizeof(DWORD),
    )

    assert statuses == [
        (False, "NET_SDK_CALLBACK_STATUS_FAILED (error_code=9)"),
        (False, "NET_SDK_CALLBACK_STATUS_EXCEPTION"),
    ]


def test_video_intercom_remote_config_reports_legacy_status_errors():
    events = []
    statuses = []

    class FakeSdk:
        def NET_DVR_StartRemoteConfig(self, *_args, **_kwargs):
            return 1

        def NET_DVR_StopRemoteConfig(self, _handle):
            return True

        def NET_DVR_GetLastError(self):
            return 0

    session = VideoIntercomRemoteConfig(
        FakeSdk(),
        1,
        on_event=events.append,
        on_status_change=lambda connected, reason=None: statuses.append((connected, reason)),
    )
    session._handle = 1
    session._handle_callback(NET_SDK_CONFIG_STATUS_FAILED, None, 0)
    session._handle_callback(NET_SDK_CONFIG_STATUS_EXCEPTION, None, 0)

    assert statuses == [
        (False, f"RemoteConfig status {NET_SDK_CONFIG_STATUS_FAILED}"),
        (False, f"RemoteConfig status {NET_SDK_CONFIG_STATUS_EXCEPTION}"),
    ]