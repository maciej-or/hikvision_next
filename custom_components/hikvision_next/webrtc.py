"""HA native WebRTC signaling with SDK video packet passthrough."""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

import av
from aiortc import (
    MediaStreamTrack,
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCRtpSender,
    RTCSessionDescription,
)
from aiortc.mediastreams import MediaStreamError
from aiortc.sdp import H264Level, H264Profile, SessionDescription, candidate_from_sdp, parse_h264_profile_level_id
from homeassistant.components.camera.webrtc import WebRTCAnswer, WebRTCError
from homeassistant.components.web_rtc import async_get_ice_servers
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import HomeAssistantError

from .sdk.media import TIME_BASE
from .sdk.rtp import configure_video_sender
from .sdk.stream import SDKPreview

_LOGGER = logging.getLogger(__name__)
SETUP_TIMEOUT = 30
PREVIEW_IDLE_TIMEOUT = 30
MAX_QUEUE_AGE = 0.3


class SDKTrack(MediaStreamTrack):
    """Bounded per-viewer queue, resuming video only at a complete IDR."""

    def __init__(self, preview, kind):
        super().__init__()
        self.kind = kind
        self.preview = preview
        self.queue = asyncio.Queue(maxsize=8 if kind == "video" else 16)
        self.wait_keyframe = kind == "video"
        self._started = False
        self.dropped = 0
        preview.consumers.add(self)

    def _clear(self):
        while not self.queue.empty():
            self.queue.get_nowait()

    def push(self, media):
        if self.readyState != "live":
            return
        if media is None:
            self._clear()
            self.wait_keyframe = self.kind == "video"
            return
        if media.kind != self.kind:
            return
        if self.queue.full():
            self.dropped += self.queue.qsize()
            self._clear()
            self.wait_keyframe = self.kind == "video"
            self.preview.request_keyframe()
        if self.wait_keyframe:
            if not media.keyframe:
                return
            self.wait_keyframe = False
        self.queue.put_nowait(media)

    async def recv(self):
        if self.readyState != "live":
            raise MediaStreamError
        if not self._started:
            self._started = True
            if self.kind == "video" and hasattr(self.preview, "gop"):
                self._clear()
                self.wait_keyframe = True
                cached = self.preview.gop.snapshot()
                for frame in cached:
                    self.push(frame)
                if not cached:
                    self.preview.request_keyframe()
                _LOGGER.debug("SDK WebRTC %s starts with %d cached frames", self.preview.stream.id, len(cached))
        while True:
            media = await self.queue.get()
            if media is None:
                raise MediaStreamError
            if time.monotonic() - media.received_at <= MAX_QUEUE_AGE:
                break
            self.dropped += 1
            self._clear()
            self.wait_keyframe = self.kind == "video"
            if self.kind == "video":
                self.preview.request_keyframe()
        if self.kind == "video":
            packet = av.Packet(media.payload)
            packet.pts = packet.dts = media.pts
            packet.time_base = TIME_BASE
            return packet
        source = media.payload
        # The Opus encoder may resample/mutate frames. Never share that frame
        # across RTCRtpSenders, which run in different executor jobs.
        frame = av.AudioFrame(format=source.format.name, layout=source.layout.name, samples=source.samples)
        for dst, src in zip(frame.planes, source.planes, strict=True):
            dst.update(bytes(src))
        frame.sample_rate = source.sample_rate
        frame.pts, frame.time_base = source.pts, source.time_base
        return frame

    def stop(self):
        if self.readyState != "live":
            return
        super().stop()
        _LOGGER.debug("SDK WebRTC %s track closed; dropped %d queued frames", self.kind, self.dropped)
        self.preview.consumers.discard(self)
        self._clear()
        self.queue.put_nowait(None)


def camera_h264_format(profile: str) -> tuple[H264Profile, H264Level, str]:
    """Classify the source and return a receiver-recognized SDP capability.

    RFC 6184 table 5 is not an exhaustive list of SPS constraint combinations.
    Some Hikvision Main encoders set constraint_set2_flag (e.g. 4d60xx),
    describing a subset of Main which aiortc/libwebrtc's table does not parse.
    Signaling the enclosing Main capability is conservative for a Main receiver.
    Only clear that extra constraint in SDP; never change SPS/PPS/video bytes,
    the level, or classify this stream as Baseline.
    """
    if not isinstance(profile, str) or re.fullmatch(r"[0-9a-fA-F]{6}", profile) is None:
        raise HomeAssistantError(f"Camera SPS contains an invalid H.264 profile-level-id: {profile!r}")
    try:
        source_profile, source_level = parse_h264_profile_level_id(profile)
        return source_profile, source_level, profile
    except ValueError as err:
        iop = int(profile[2:4], 16)
        if profile[:2].lower() == "4d" and iop & 0xAF == 0x20:
            compatible = f"4d{iop & ~0x20:02x}{profile[4:6]}"
            try:
                source_profile, source_level = parse_h264_profile_level_id(compatible)
                return source_profile, source_level, compatible
            except ValueError:
                pass
        raise HomeAssistantError(f"Camera H.264 profile/level {profile} is unsupported by the WebRTC adapter") from err


def prepare_offer(offer_sdp: str, profile: str) -> tuple[str, dict]:
    """Select a browser-supported profile matching the actual SPS.

    aiortc 1.14's encoder advertises Baseline only. Its encoded-packet path can
    packetize Main/High too. Translate *only the internal* H.264 profile during
    negotiation, then restore the source's compatible fmtp in the browser-facing
    answer. Never change SPS or select a profile the browser did not offer. No global
    aiortc codec tables or other integrations are modified.
    """
    description = SessionDescription.parse(offer_sdp)
    source_profile, source_level, signaling_profile = camera_h264_format(profile)
    if signaling_profile != profile:
        _LOGGER.debug(
            "Camera SPS %s uses constrained Main; SDP advertises Main capability %s", profile, signaling_profile
        )
    for media in description.media:
        if media.kind != "video" or media.port == 0:
            continue
        if media.direction not in ("recvonly", "sendrecv", None):
            continue
        matches = []
        advertised = []
        for codec in media.rtp.codecs:
            if codec.mimeType.lower() != "video/h264" or str(codec.parameters.get("packetization-mode", 0)) != "1":
                continue
            advertised.append(str(codec.parameters.get("profile-level-id", "42e01f")))
            try:
                offered_profile, offered_level = parse_h264_profile_level_id(
                    codec.parameters.get("profile-level-id", "42e01f")
                )
            except ValueError:
                continue
            asymmetric = str(codec.parameters.get("level-asymmetry-allowed", "0")) == "1"
            if offered_profile == source_profile and (offered_level >= source_level or asymmetric):
                matches.append((offered_level >= source_level, offered_level, codec))
        if not matches:
            raise HomeAssistantError(
                f"Browser does not offer a compatible H.264 configuration for camera {profile} "
                f"(browser offers: {', '.join(advertised) or 'no packetization-mode=1 H.264'}); "
                "select a compatible H.264 sub-stream or lower the camera resolution/level"
            )
        # Prefer a payload explicitly advertising sufficient receive capability.
        # Asymmetric offers can also try best-effort packet passthrough above a
        # conservative default level. Level asymmetry itself does NOT increase
        # the receiver's advertised limit or prove decoding support. Keep the
        # true SPS and answer level; never relabel or silently transcode video.
        sufficient_level, offered_level, selected = max(matches, key=lambda match: match[:2])
        if not sufficient_level:
            _LOGGER.debug(
                "Trying H.264 %s passthrough above advertised receive level %s; "
                "receiver decoding support is unverified",
                profile,
                offered_level.name,
            )
        params = dict(selected.parameters)
        params["profile-level-id"] = signaling_profile
        payload = selected.payloadType
        media.rtp.codecs = [selected] + [
            codec
            for codec in media.rtp.codecs
            if codec.mimeType.lower() == "video/rtx" and codec.parameters.get("apt") == payload
        ]
        media.fmt = [codec.payloadType for codec in media.rtp.codecs]
        selected.parameters = {**selected.parameters, "profile-level-id": "42e01f"}
        return str(description), {payload: params}
    raise HomeAssistantError("WebRTC offer contains no receiving H.264 video track")


def restore_answer(answer_sdp: str, parameters: dict) -> str:
    """Restore the camera's compatible H.264 capability on browser-facing SDP."""
    description = SessionDescription.parse(answer_sdp)
    for media in description.media:
        for codec in media.rtp.codecs:
            if media.kind == "video" and codec.payloadType in parameters:
                codec.parameters = parameters[codec.payloadType]
    return str(description)


@dataclass
class Session:
    pc: RTCPeerConnection
    send_message: object
    preview: SDKPreview | None = None
    tracks: list = field(default_factory=list)
    pending_candidates: list = field(default_factory=list)
    closed: bool = False
    timer: asyncio.TimerHandle | None = None
    setup_task: asyncio.Task | None = None
    created: float = field(default_factory=time.monotonic)


class SDKWebRTCManager:
    """Own sessions and shared previews; all public methods run on HA's loop."""

    def __init__(self, device):
        self.device = device
        self.hass = device.hass
        self.sessions = {}
        self.previews = {}
        self._lock = asyncio.Lock()
        self._closing = False
        self._cleanup_tasks = set()
        self._idle_timers = {}
        self._unsub_stop = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._stop)

    async def _stop(self, event):
        await self.close()

    async def offer(self, stream, offer_sdp, session_id, send_message):
        if self._closing or session_id in self.sessions:
            raise HomeAssistantError("Camera WebRTC session unavailable")
        servers = [
            RTCIceServer(urls=server.urls, username=server.username, credential=server.credential)
            for server in async_get_ice_servers(self.hass)
        ]
        pc = RTCPeerConnection(RTCConfiguration(iceServers=servers))
        session = Session(pc, send_message)
        session.setup_task = asyncio.current_task()
        self.sessions[session_id] = session
        session.timer = asyncio.get_running_loop().call_later(SETUP_TIMEOUT, self.close_session, session_id)

        @pc.on("connectionstatechange")
        async def state_changed():
            if pc.connectionState == "connected":
                _LOGGER.debug("SDK WebRTC connected after %.3fs", time.monotonic() - session.created)
                if session.timer:
                    session.timer.cancel()
                    session.timer = None
            elif pc.connectionState in ("failed", "closed"):
                if not session.closed and pc.connectionState == "failed":
                    send_message(WebRTCError("stream_failed", "WebRTC media connection failed"))
                self.close_session(session_id)

        try:
            async with asyncio.timeout(SETUP_TIMEOUT):
                async with self._lock:
                    if session.closed or self._closing:
                        raise HomeAssistantError("Camera WebRTC session closed")
                    preview = self.previews.get(stream.id)
                    if timer := self._idle_timers.pop(stream.id, None):
                        timer.cancel()
                    if preview is None:
                        preview = self.previews[stream.id] = SDKPreview(self.device, stream)
                        _LOGGER.debug("SDK WebRTC %s starting cold preview", stream.id)
                    else:
                        _LOGGER.debug("SDK WebRTC %s reusing warm preview", stream.id)
                    preview.references += 1
                    session.preview = preview
                await asyncio.wait_for(preview.ready.wait(), 12)
                if preview.error:
                    raise HomeAssistantError(preview.error)
                if session.closed:
                    return
                # Start obtaining a fresh IDR while SDP/ICE is negotiated,
                # rather than waiting for the sender's first recv().
                if not preview.gop.snapshot():
                    preview.request_keyframe()
                internal_sdp, params = prepare_offer(offer_sdp, preview.profile)
                await pc.setRemoteDescription(RTCSessionDescription(sdp=internal_sdp, type="offer"))
                for candidate in session.pending_candidates:
                    await pc.addIceCandidate(candidate)
                session.pending_candidates.clear()
                video = SDKTrack(preview, "video")
                session.tracks.append(video)
                sender = pc.addTrack(video)
                configure_video_sender(sender)
                # aiortc's encoded-packet path cannot force its encoder to emit
                # an IDR. Forward RTCP PLI/FIR to the source SDK instead.
                sender._send_keyframe = preview.request_keyframe
                for transceiver in pc.getTransceivers():
                    if transceiver.kind == "video":
                        transceiver.direction = "sendonly"
                        transceiver.setCodecPreferences(
                            [
                                c
                                for c in RTCRtpSender.getCapabilities("video").codecs
                                if c.mimeType.lower() == "video/rtx"
                                or (
                                    c.mimeType.lower() == "video/h264"
                                    and c.parameters.get("profile-level-id") == "42e01f"
                                )
                            ]
                        )
                    elif transceiver.kind == "audio":
                        if stream.audio:
                            audio = SDKTrack(preview, "audio")
                            session.tracks.append(audio)
                            pc.addTrack(audio)
                            transceiver.direction = "sendonly"
                            transceiver.setCodecPreferences(
                                [
                                    c
                                    for c in RTCRtpSender.getCapabilities("audio").codecs
                                    if c.mimeType.lower() == "audio/opus"
                                ]
                            )
                        else:
                            transceiver.direction = "inactive"
                await pc.setLocalDescription(await pc.createAnswer())
                if not session.closed:
                    _LOGGER.debug(
                        "SDK WebRTC %s answer ready after %.3fs", stream.id, time.monotonic() - session.created
                    )
                    send_message(WebRTCAnswer(restore_answer(pc.localDescription.sdp, params)))
        except asyncio.CancelledError:
            self.close_session(session_id)
            raise
        except Exception as err:
            self.close_session(session_id)
            _LOGGER.exception("SDK WebRTC setup failed for stream %s", stream.id)
            if isinstance(err, HomeAssistantError):
                raise
            raise HomeAssistantError(f"SDK WebRTC setup failed ({type(err).__name__})") from err
        finally:
            session.setup_task = None

    async def candidate(self, session_id, candidate):
        session = self.sessions.get(session_id)
        if session is None or session.closed:
            return
        value = candidate.candidate
        parsed = None
        if value:
            try:
                parsed = candidate_from_sdp(value.removeprefix("candidate:"))
            except (ValueError, AssertionError) as err:
                raise HomeAssistantError("Invalid WebRTC ICE candidate") from err
            parsed.sdpMid = candidate.sdp_mid
            parsed.sdpMLineIndex = candidate.sdp_m_line_index
        if session.pc.remoteDescription is None:
            if len(session.pending_candidates) >= 64:
                raise HomeAssistantError("Too many pending ICE candidates")
            session.pending_candidates.append(parsed)
        else:
            await session.pc.addIceCandidate(parsed)

    def close_session(self, session_id):
        session = self.sessions.pop(session_id, None)
        if session is None:
            return
        session.closed = True
        if session.setup_task and session.setup_task is not asyncio.current_task():
            session.setup_task.cancel()
        if session.timer:
            session.timer.cancel()
        for track in session.tracks:
            track.stop()
        task = self.hass.async_create_task(self._dispose(session), "hikvision_webrtc_close")
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _dispose(self, session):
        try:
            await session.pc.close()
            for sender in session.pc.getSenders():
                if hasattr(sender, "_hikvision_retransmits"):
                    _LOGGER.debug(
                        "SDK WebRTC video closed: pacing yields=%d, retransmits=%d, NACK misses=%d",
                        sender.transport._hikvision_pacing_yields,
                        sender._hikvision_retransmits,
                        sender._hikvision_nack_misses,
                    )
        finally:
            async with self._lock:
                if preview := session.preview:
                    preview.references -= 1
                    if preview.references == 0:
                        if self._closing:
                            self.previews.pop(preview.stream.id, None)
                            await preview.close()
                        else:
                            self._idle_timers[preview.stream.id] = asyncio.get_running_loop().call_later(
                                PREVIEW_IDLE_TIMEOUT, self._expire_preview, preview
                            )

    def _expire_preview(self, preview):
        self._idle_timers.pop(preview.stream.id, None)
        if preview.references or self.previews.get(preview.stream.id) is not preview:
            return
        self.previews.pop(preview.stream.id)
        task = self.hass.async_create_task(preview.close(), "hikvision_webrtc_idle_close")
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def close(self):
        self._closing = True
        self._unsub_stop()
        for session_id in list(self.sessions):
            self.close_session(session_id)
        if self._cleanup_tasks:
            await asyncio.gather(*list(self._cleanup_tasks))
        for timer in self._idle_timers.values():
            timer.cancel()
        self._idle_timers.clear()
        previews = list(self.previews.values())
        self.previews.clear()
        await asyncio.gather(*(preview.close() for preview in previews))
