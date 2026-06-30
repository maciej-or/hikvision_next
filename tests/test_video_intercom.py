"""Tests for video intercom RemoteConfig helpers."""

from custom_components.hikvision_next.sdk.hcnetsdk import VideoCallCmdType
from custom_components.hikvision_next.sdk.video_intercom import (
    IntercomCallState,
    build_video_call_param,
    intercom_sensors_for_state,
    is_video_intercom_device,
    map_video_call_cmd_to_doorbell,
    map_video_call_cmd_to_in_call,
    next_intercom_call_state,
    parse_video_call_param,
)


def test_is_video_intercom_device_by_model():
    assert is_video_intercom_device(model="DS-KD8003-E6")
    assert not is_video_intercom_device(model="DS-2CD2143G2-I")


def test_is_video_intercom_device_by_type():
    assert is_video_intercom_device(device_type="Video Intercom")
    assert is_video_intercom_device(device_type="Door Station")
    assert not is_video_intercom_device(device_type="IPCamera")


def test_map_video_call_cmd_to_doorbell():
    assert map_video_call_cmd_to_doorbell(VideoCallCmdType.CALLING) is True
    assert map_video_call_cmd_to_doorbell(VideoCallCmdType.INDOOR_STATION_RINGING) is True
    assert map_video_call_cmd_to_doorbell(VideoCallCmdType.REJECT_CALL) is False
    assert map_video_call_cmd_to_doorbell(VideoCallCmdType.END_CALL) is False
    assert map_video_call_cmd_to_doorbell(VideoCallCmdType.ANSWER_CALL) is None


def test_map_video_call_cmd_to_in_call():
    assert map_video_call_cmd_to_in_call(VideoCallCmdType.DEVICE_IN_CALL) is True
    assert map_video_call_cmd_to_in_call(VideoCallCmdType.CLIENT_IN_CALL) is True
    assert map_video_call_cmd_to_in_call(VideoCallCmdType.CALLING) is False
    assert map_video_call_cmd_to_in_call(VideoCallCmdType.INDOOR_STATION_RINGING) is False
    assert map_video_call_cmd_to_in_call(VideoCallCmdType.END_CALL) is False
    assert map_video_call_cmd_to_in_call(VideoCallCmdType.REJECT_CALL) is False
    assert map_video_call_cmd_to_in_call(VideoCallCmdType.CANCEL_CALL) is False


def test_intercom_call_state_machine_hangup_then_ring():
    state = IntercomCallState.IN_CALL
    state = next_intercom_call_state(VideoCallCmdType.END_CALL, state)
    assert state == IntercomCallState.IDLE
    assert intercom_sensors_for_state(state) == (False, False)

    state = next_intercom_call_state(VideoCallCmdType.CALLING, state)
    assert state == IntercomCallState.RINGING
    assert intercom_sensors_for_state(state) == (True, False)


def test_intercom_call_state_machine_answer_flow():
    state = IntercomCallState.IDLE
    state = next_intercom_call_state(VideoCallCmdType.CALLING, state)
    assert intercom_sensors_for_state(state) == (True, False)

    state = next_intercom_call_state(VideoCallCmdType.ANSWER_CALL, state)
    assert state == IntercomCallState.IN_CALL
    assert intercom_sensors_for_state(state) == (False, True)


def test_intercom_call_state_machine_ignores_stale_ringing_while_in_call():
    state = IntercomCallState.IN_CALL
    assert next_intercom_call_state(VideoCallCmdType.CALLING, state) is None
    assert next_intercom_call_state(VideoCallCmdType.INDOOR_STATION_RINGING, state) is None


def test_parse_video_call_param():
    param = build_video_call_param(
        VideoCallCmdType.CALLING,
        building_number=1,
        unit_number=2,
        room_number=3,
        dev_index=4,
    )
    event = parse_video_call_param(param)
    assert event.cmd_type == VideoCallCmdType.CALLING
    assert event.building_number == 1
    assert event.unit_number == 2
    assert event.room_number == 3
    assert event.dev_index == 4
    assert event.doorbell_active is True
    assert event.in_call_active is False


def test_parse_video_call_param_in_call():
    param = build_video_call_param(VideoCallCmdType.DEVICE_IN_CALL, dev_index=2)
    event = parse_video_call_param(param)
    assert event.cmd_type == VideoCallCmdType.DEVICE_IN_CALL
    assert event.doorbell_active is None
    assert event.in_call_active is True