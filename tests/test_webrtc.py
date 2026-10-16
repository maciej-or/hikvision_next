"""WebRTC negotiation, bounded fan-out and real local RTP decoding."""

import asyncio
import threading
from ctypes import POINTER, byref, c_void_p, cast, create_string_buffer
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import av
import pytest
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from homeassistant.components.camera.webrtc import WebRTCAnswer
from homeassistant.exceptions import HomeAssistantError

from custom_components.hikvision_next.sdk.media import TIME_BASE, Media, nal_units
from custom_components.hikvision_next.sdk.preview import NET_DVR_PREVIEWINFO, PacketInfo
from custom_components.hikvision_next.webrtc import (
    SDKTrack,
    SDKWebRTCManager,
    camera_h264_format,
    prepare_offer,
    restore_answer,
)
from tests import test_sdk_media
from tests.test_sdk_media import pes

video_packets = test_sdk_media.video_packets


@pytest.fixture(autouse=True)
def local_ice_addresses():
    """HA's network fixture uses a fake NIC; real RTP stays on loopback."""
    with patch("aioice.ice.get_host_addresses", return_value=["127.0.0.1"]):
        yield


class FakeSDK:
    """A native-like SDK producing timed ES or fragmented PS callbacks."""

    def __init__(self, packets, es=True):
        self.packets, self.es = packets, es
        self.sources = {}
        self.counter = 0
        self.NET_DVR_RealPlay_V40 = Mock(side_effect=self._start)
        self.NET_DVR_StopRealPlay = Mock(side_effect=self._stop)
        self.NET_DVR_SetESRealPlayCallBack = Mock(side_effect=self._set_es)
        self.NET_DVR_MakeKeyFrame = Mock(side_effect=self._keyframe)
        self.NET_DVR_MakeKeyFrameSub = Mock(side_effect=self._keyframe)
        self.NET_DVR_GetLastError = Mock(return_value=0)

    def _start(self, user, info, callback, context):
        self.counter += 1
        handle = self.counter
        config = cast(info, POINTER(NET_DVR_PREVIEWINFO)).contents
        state = self.sources[handle] = SimpleNamespace(
            stop=threading.Event(),
            keyframe=threading.Event(),
            es=None,
            channel=config.lChannel,
            stream=config.dwStreamType,
        )

        def produce():
            tick = index = 0
            while not state.stop.wait(0.04):
                if state.keyframe.is_set():
                    index = 0
                    state.keyframe.clear()
                packet = self.packets[index % len(self.packets)]
                if state.es:
                    self._emit_es(state.es, bytes(packet), tick * 40, 1 if packet.is_keyframe else 3)
                    self._emit_es(state.es, bytes([0xD5]) * 320, tick * 40, 10)
                else:
                    data = pes(bytes(packet), tick * 3600)
                    for offset in range(0, len(data), 67):
                        part = data[offset : offset + 67]
                        buf = create_string_buffer(part)
                        callback(handle, 2, cast(buf, c_void_p), len(part), None)
                tick += 1
                index += 1

        state.thread = threading.Thread(target=produce, daemon=True)
        state.thread.start()
        return handle

    def _set_es(self, handle, callback, context):
        if self.es is True:
            self.sources[handle].es = callback
            return 1
        return 1 if self.es == "silent" else 0

    @staticmethod
    def _emit_es(callback, data, pts, kind):
        buffer = create_string_buffer(data)
        info = PacketInfo()
        info.dwTimeStamp, info.dwPacketType, info.dwPacketSize = pts, kind, len(data)
        info.pPacketBuffer = cast(buffer, c_void_p)
        callback(1, byref(info), None)

    def _keyframe(self, user, channel):
        for state in self.sources.values():
            state.keyframe.set()
        return 1

    def _stop(self, handle):
        source = self.sources.pop(handle)
        source.stop.set()
        source.thread.join()
        return 1


def stream_info(audio=True):
    return SimpleNamespace(id=3301, type_id=1, audio=audio, audio_codec="G.711alaw", audio_sample_rate=8000)


def manager_for(sdk):
    hass = SimpleNamespace(
        async_create_task=lambda coro, name: asyncio.create_task(coro, name=name),
        bus=SimpleNamespace(async_listen_once=Mock(return_value=Mock())),
    )
    device = SimpleNamespace(sdk_subscription=sdk, sdk_user=0, hass=hass)
    return SDKWebRTCManager(device)


async def wait_until(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_track_drops_until_keyframe_and_clones_audio():
    preview = SimpleNamespace(consumers=set(), request_keyframe=Mock())
    track = SDKTrack(preview, "video")
    delta = Media("video", b"delta", 90)
    key = Media("video", b"key", 0, True)
    track.push(delta)
    assert track.queue.empty()
    track.push(key)
    for _ in range(8):
        track.push(delta)
    assert track.queue.empty() and track.wait_keyframe
    track.push(key)
    received = await track.recv()
    assert isinstance(received, av.Packet)
    assert bytes(received) == b"key" and received.time_base == TIME_BASE
    track.stop()
    assert not preview.consumers
    audio = SDKTrack(preview, "audio")
    frame = av.AudioFrame(format="s16", layout="mono", samples=160)
    frame.sample_rate, frame.pts, frame.time_base = 8000, 160, Fraction(1, 8000)
    for plane in frame.planes:
        plane.update(bytes(plane.buffer_size))
    audio.push(Media("audio", frame, 1800))
    cloned = await audio.recv()
    assert cloned is not frame and cloned.pts == frame.pts
    assert bytes(cloned.planes[0]) == bytes(frame.planes[0])
    audio.stop()


async def test_profile_negotiation_rejects_unsupported_and_restores_high():
    client = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    try:
        client.addTransceiver("video", direction="recvonly")
        offer = (await client.createOffer()).sdp
        with pytest.raises(HomeAssistantError, match="does not offer"):
            prepare_offer(offer, "64001f")
        high_offer = offer.replace("profile-level-id=42e01f", "profile-level-id=640033")
        internal, parameters = prepare_offer(high_offer, "640028")
        assert "profile-level-id=42e01f" in internal
        restored = restore_answer(internal, parameters)
        assert "profile-level-id=640028" in restored
    finally:
        await client.close()


@pytest.mark.parametrize("profile", ["420032", "4d0032"])
async def test_level5_passthrough_for_asymmetric_offer(profile):
    client = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    try:
        client.addTransceiver("video", direction="recvonly")
        offer = (await client.createOffer()).sdp
        if profile.startswith("4d"):
            offer = offer.replace("profile-level-id=42001f", "profile-level-id=4d001f")
        internal, parameters = prepare_offer(offer, profile)
        assert "profile-level-id=42e01f" in internal
        assert f"profile-level-id={profile}" in restore_answer(internal, parameters)
        # Without permission for asymmetric levels, do not advertise a higher
        # level than this offer accepts. Profile incompatibility is also kept.
        with pytest.raises(HomeAssistantError, match="browser offers"):
            prepare_offer(offer.replace("level-asymmetry-allowed=1", "level-asymmetry-allowed=0"), profile)
        with pytest.raises(HomeAssistantError, match="does not offer"):
            prepare_offer(offer, "640032")
    finally:
        await client.close()


@pytest.mark.parametrize(
    ("source", "signaling"),
    [("4d6034", "4d4034"), ("4d2032", "4d0032"), ("4d3032", "4d1032"), ("4d7032", "4d5032")],
)
async def test_main_with_extra_constraints(source, signaling):
    from aiortc.sdp import H264Level, H264Profile, parse_h264_profile_level_id

    actual_profile, level, normalized = camera_h264_format(source)
    assert actual_profile == H264Profile.PROFILE_MAIN
    assert level == H264Level(int(source[4:6], 16))
    assert normalized == signaling
    assert parse_h264_profile_level_id(normalized) == (actual_profile, level)
    client = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    try:
        client.addTransceiver("video", direction="recvonly")
        offer = (await client.createOffer()).sdp
        # Constrained Main is still not Baseline. Require a Main receiver.
        with pytest.raises(HomeAssistantError, match="does not offer"):
            prepare_offer(offer, source)
        main_offer = offer.replace("profile-level-id=42001f", "profile-level-id=4d001f")
        internal, params = prepare_offer(main_offer, source)
        assert f"profile-level-id={signaling}" in restore_answer(internal, params)
        assert f"profile-level-id={source}" not in restore_answer(internal, params)
    finally:
        await client.close()


@pytest.mark.parametrize("profile", [None, "invalid", "4d63ff", "4d20ff", "64e034"])
def test_invalid_source_profile_has_actionable_error(profile):
    with pytest.raises(HomeAssistantError, match="Camera .*profile"):
        camera_h264_format(profile)


@pytest.mark.enable_socket
@pytest.mark.parametrize("es", [True, False, "silent", "level5", "constrained_main"])
async def test_real_webrtc_rtp_decode_and_release(video_packets, es, socket_enabled, monkeypatch):
    if es in ("level5", "constrained_main"):
        # A valid low-resolution Baseline stream can declare Level 5.0 too.
        # Reproduce exactly 420032 and verify its unchanged packets decode
        # through real RTP after a browser-like Level 3.1 offer.
        profile = b"\x4d\x60\x34" if es == "constrained_main" else b"\x42\x00\x32"
        if es == "constrained_main":
            from aiortc import RTCRtpCodecParameters
            from aiortc.codecs import CODECS

            # This test receiver supports Main decoding, as a real Main-capable
            # browser does. Do not modify the production codec registry.
            monkeypatch.setitem(
                CODECS,
                "video",
                [
                    *CODECS["video"],
                    RTCRtpCodecParameters(
                        mimeType="video/H264",
                        clockRate=90000,
                        payloadType=120,
                        parameters={
                            "profile-level-id": "4d0034",
                            "packetization-mode": "1",
                            "level-asymmetry-allowed": "1",
                        },
                    ),
                ],
            )
        packets = []
        for packet in video_packets:
            units = [b"\x67" + profile + unit[4:] if unit[0] & 31 == 7 else unit for unit in nal_units(bytes(packet))]
            elevated = av.Packet(b"\x00\x00\x00\x01" + b"\x00\x00\x00\x01".join(units))
            elevated.is_keyframe = packet.is_keyframe
            packets.append(elevated)
        sdk = FakeSDK(packets, es=True)
    else:
        sdk = FakeSDK(video_packets, es=es)
    manager = manager_for(sdk)
    client = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    received_tracks = {}

    @client.on("track")
    def on_track(track):
        received_tracks[track.kind] = track

    try:
        client.addTransceiver("video", direction="recvonly")
        if es is True:
            client.addTransceiver("audio", direction="recvonly")
        await client.setLocalDescription(await client.createOffer())
        messages = []
        with patch("custom_components.hikvision_next.webrtc.async_get_ice_servers", return_value=[]):
            await manager.offer(stream_info(audio=es is True), client.localDescription.sdp, "session", messages.append)
        if es == "level5":
            assert manager.previews[3301].profile == "420032"
        answer = next(m.answer for m in messages if isinstance(m, WebRTCAnswer))
        if es == "constrained_main":
            assert manager.previews[3301].profile == "4d6034"
            assert "profile-level-id=4d4034" in answer
            sps = next(unit for unit in nal_units(manager.previews[3301].gop.frames[0].payload) if unit[0] & 31 == 7)
            assert sps[1:4] == b"\x4d\x60\x34"
        await client.setRemoteDescription(RTCSessionDescription(answer, "answer"))
        video = await asyncio.wait_for(received_tracks["video"].recv(), 8)
        assert (video.width, video.height) == (64, 48)
        if es is True:
            audio = await asyncio.wait_for(received_tracks["audio"].recv(), 8)
            assert audio.sample_rate == 48000
        preview = manager.previews[3301]
        assert sdk.sources[1].channel == 33
        assert sdk.sources[1].stream == 0
        # Simulate input loss/overflow: existing peer survives a new SDK handle.
        preview._overflow.set()
        await wait_until(lambda: sdk.counter >= 2)
        await wait_until(lambda: not any(t.wait_keyframe for t in preview.consumers if t.kind == "video"))
        next_frame = await asyncio.wait_for(received_tracks["video"].recv(), 8)
        assert next_frame.width == 64
        if es is True:
            # Discard buffered receiver audio to verify *new* packets after
            # the preview restart and the source timestamp discontinuity.
            while not received_tracks["audio"]._queue.empty():
                received_tracks["audio"]._queue.get_nowait()
            resumed_audio = await asyncio.wait_for(received_tracks["audio"].recv(), 5)
            assert resumed_audio.pts > audio.pts
    finally:
        await manager.close()
        await client.close()
    assert not manager.previews and not manager.sessions
    assert not sdk.sources
    assert sdk.NET_DVR_StopRealPlay.call_count >= 2


@pytest.mark.enable_socket
async def test_shared_preview_two_sessions(video_packets, socket_enabled):
    sdk = FakeSDK(video_packets)
    manager = manager_for(sdk)
    clients = [RTCPeerConnection(RTCConfiguration(iceServers=[])) for _ in range(2)]
    try:
        for index, client in enumerate(clients):
            client.addTransceiver("video", direction="recvonly")
            # No local ICE gathering is necessary to test shared allocation.
            offer = await client.createOffer()
            with patch("custom_components.hikvision_next.webrtc.async_get_ice_servers", return_value=[]):
                await manager.offer(stream_info(False), offer.sdp, str(index), lambda msg: None)
        assert sdk.NET_DVR_RealPlay_V40.call_count == 1
        assert manager.previews[3301].references == 2
        manager.close_session("0")
        await wait_until(lambda: manager.previews[3301].references == 1)
        assert sdk.NET_DVR_StopRealPlay.call_count == 0
        manager.close_session("1")
        await wait_until(lambda: manager.previews[3301].references == 0)
        assert sdk.NET_DVR_StopRealPlay.call_count == 0
        # Reopening during the idle grace period reuses the native handle.
        with patch("custom_components.hikvision_next.webrtc.async_get_ice_servers", return_value=[]):
            await manager.offer(stream_info(False), offer.sdp, "warm", lambda msg: None)
        assert sdk.NET_DVR_RealPlay_V40.call_count == 1
        assert 3301 not in manager._idle_timers
        with patch("custom_components.hikvision_next.webrtc.PREVIEW_IDLE_TIMEOUT", 0.02):
            manager.close_session("warm")
            await wait_until(lambda: not manager.previews and not sdk.sources)
        assert sdk.NET_DVR_StopRealPlay.call_count == 1
    finally:
        await manager.close()
        for client in clients:
            await client.close()


@pytest.mark.enable_socket
async def test_failed_setup_releases_preview(video_packets, socket_enabled):
    sdk = FakeSDK(video_packets)
    manager = manager_for(sdk)
    try:
        with (
            patch("custom_components.hikvision_next.webrtc.async_get_ice_servers", return_value=[]),
            pytest.raises(HomeAssistantError),
        ):
            await manager.offer(stream_info(), "bad SDP", "failed", lambda msg: None)
    finally:
        await manager.close()
    assert not manager.sessions and not manager.previews and not sdk.sources


async def test_close_during_setup_and_early_ice_candidate():
    from webrtc_models import RTCIceCandidateInit

    manager = manager_for(None)
    stream = stream_info()
    preview = SimpleNamespace(
        stream=stream,
        ready=asyncio.Event(),
        references=0,
        close=AsyncMock(),
    )
    try:
        with (
            patch("custom_components.hikvision_next.webrtc.SDKPreview", return_value=preview),
            patch("custom_components.hikvision_next.webrtc.async_get_ice_servers", return_value=[]),
        ):
            task = asyncio.create_task(manager.offer(stream, "pending SDP", "pending", lambda msg: None))
            await wait_until(lambda: preview.references == 1)
            await manager.candidate("pending", RTCIceCandidateInit(candidate=""))
            assert manager.sessions["pending"].pending_candidates == [None]
            with pytest.raises(HomeAssistantError, match="Invalid.*candidate"):
                await manager.candidate("pending", RTCIceCandidateInit(candidate="invalid"))
            manager.close_session("pending")
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        await manager.close()
    preview.close.assert_awaited_once()
    assert not manager.sessions and not manager.previews


async def test_cached_gop_and_stale_queue():
    import time

    from custom_components.hikvision_next.sdk.stream import GOPCache

    gop = GOPCache()
    preview = SimpleNamespace(gop=gop, consumers=set(), request_keyframe=Mock(), stream=stream_info())
    key = Media("video", b"key", 0, True)
    delta = Media("video", b"delta", 3600)
    gop.push(key)
    gop.push(delta)
    track = SDKTrack(preview, "video")
    assert bytes(await track.recv()) == b"key"
    assert bytes(await track.recv()) == b"delta"
    # Never pass queued stale frames to RTP, even below the queue length cap.
    track.push(Media("video", b"stale", 7200, received_at=time.monotonic() - 1))
    pending = asyncio.create_task(track.recv())
    await wait_until(lambda: preview.request_keyframe.called)
    assert not pending.done() and track.wait_keyframe
    track.push(Media("video", b"skip", 10800))
    track.push(Media("video", b"new key", 14400, True))
    assert bytes(await pending) == b"new key"
    track.stop()
    # Staleness and capacity invalidate the entire dependency chain.
    gop.clear()
    gop.push(Media("video", b"old", 0, True, received_at=time.monotonic() - 1))
    assert not gop.snapshot()
    gop.push(key)
    for _ in range(8):
        gop.push(delta)
    assert not gop.snapshot()
    gop.push(key)
    gop.push(None)
    assert not gop.snapshot()


async def test_paced_large_frame_and_nack_history():
    from aiortc import RTCRtpSender
    from aiortc.mediastreams import MediaStreamError
    from aiortc.rtcrtpparameters import RTCRtpCodecParameters
    from aiortc.rtcrtpsender import RTCEncodedFrame
    from aiortc.rtp import RTCP_RTPFB_NACK, RtcpRtpfbPacket, RtpPacket

    from custom_components.hikvision_next.sdk.rtp import configure_video_sender

    # Exercise aiortc's real _run_rtp path with a >128-packet frame.
    transport = SimpleNamespace(state="connected", _send_rtp=AsyncMock())
    track = SDKTrack(SimpleNamespace(consumers=set(), request_keyframe=Mock()), "video")
    sender = RTCRtpSender(track, transport)
    unaffected = RTCRtpSender("video", SimpleNamespace(state="connected", _send_rtp=AsyncMock()))
    configure_video_sender(sender)
    sender._next_encoded_frame = AsyncMock(
        side_effect=[RTCEncodedFrame([b"x" * 1200] * 500, 0, None), MediaStreamError()]
    )
    sleeps = []
    real_sleep = asyncio.sleep

    async def yielding_sleep(delay):
        sleeps.append(delay)
        await real_sleep(0)

    with patch("custom_components.hikvision_next.sdk.rtp.asyncio.sleep", yielding_sleep):
        await sender._run_rtp(RTCRtpCodecParameters(mimeType="video/H264", clockRate=90000, payloadType=99))
    sent = transport._send_rtp  # pacing wrapper, underlying mock retained below
    history = sender._RTCRtpSender__rtp_history
    assert len(history.packets) == 500 and len(sleeps) >= 31
    first = next(iter(history.packets.values()))
    wire = []
    transport._send_rtp = AsyncMock(side_effect=lambda data: wire.append(data))
    sender._RTCRtpSender__rtx_payload_type = 100
    await sender._handle_rtcp_packet(
        RtcpRtpfbPacket(fmt=RTCP_RTPFB_NACK, ssrc=123, media_ssrc=sender._ssrc, lost=[first.sequence_number])
    )
    retry = RtpPacket.parse(wire[0])
    assert retry.payload_type == 100
    assert retry.payload[:2] == first.sequence_number.to_bytes(2, "big")
    assert retry.payload[2:] == first.payload
    assert sender._hikvision_retransmits == 1
    assert isinstance(unaffected._RTCRtpSender__rtp_history, dict)
    # Audio and RTCP bypass the video pacer on the same transport.
    audio = RtpPacket(payload_type=111, sequence_number=1, timestamp=0)
    audio.ssrc = sender._ssrc ^ 1
    with patch("custom_components.hikvision_next.sdk.rtp.asyncio.sleep", yielding_sleep):
        count = len(sleeps)
        for _ in range(32):
            await sent(audio.serialize())
            await sent(bytes([0x80, 200]) + bytes(26))
        assert len(sleeps) == count


async def test_close_sender_during_pacing():
    from aiortc import RTCRtpSender
    from aiortc.rtcrtpparameters import RTCRtpCodecParameters
    from aiortc.rtcrtpsender import RTCEncodedFrame

    from custom_components.hikvision_next.sdk.rtp import configure_video_sender

    preview = SimpleNamespace(consumers=set(), request_keyframe=Mock())
    sender = RTCRtpSender(SDKTrack(preview, "video"), SimpleNamespace(state="connected", _send_rtp=AsyncMock()))
    configure_video_sender(sender)
    sender._next_encoded_frame = AsyncMock(return_value=RTCEncodedFrame([b"x"] * 500, 0, None))
    task = asyncio.create_task(
        sender._run_rtp(RTCRtpCodecParameters(mimeType="video/H264", clockRate=90000, payloadType=99))
    )
    await wait_until(lambda: sender.transport._hikvision_pacing_yields > 0)
    task.cancel()
    await asyncio.wait_for(task, 1)
    assert sender._RTCRtpSender__rtp_exited.is_set()
    assert not preview.consumers


def test_history_limits_and_sequence_wrap():
    from aiortc.rtp import RtpPacket

    from custom_components.hikvision_next.sdk.rtp import PacketHistory

    history = PacketHistory()
    for index in range(2200):
        packet = RtpPacket(sequence_number=(65000 + index) & 65535)
        packet.payload = bytes(1200)
        history[index % 128] = packet
    assert len(history.packets) == 2048
    assert history.get(65000) is None
    assert history.get(0).sequence_number == 0
    for index in range(20):
        packet = RtpPacket(sequence_number=index)
        packet.payload = bytes(1024 * 1024)
        history[0] = packet
    assert history.size <= 8 * 1024 * 1024
