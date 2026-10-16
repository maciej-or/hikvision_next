"""One on-demand SDK preview and parsing worker per channel / stream."""

import asyncio
import logging
import queue
import threading
import time
from collections import deque
from ctypes import byref, string_at

from .hcnetsdk import NET_DVR_PREVIEWINFO
from .media import MAX_BUFFER, MediaProcessor
from .preview import STREAMDATA, SYSHEAD, ESCallback, RealDataCallback, bind_preview, preview_channel

_LOGGER = logging.getLogger(__name__)


class GOPCache:
    """Only offer a complete, recent GOP; never replay a partial dependency chain."""

    def __init__(self):
        self.frames = []
        self.size = 0

    def clear(self):
        self.frames.clear()
        self.size = 0

    def push(self, media):
        if media is None:
            self.clear()
        elif media.kind == "video":
            if media.keyframe:
                self.clear()
                self.frames.append(media)
                self.size = len(media.payload)
            elif self.frames:
                self.frames.append(media)
                self.size += len(media.payload)
            if len(self.frames) > 8 or self.size > 4 * 1024 * 1024:
                self.clear()

    def snapshot(self):
        if self.frames and time.monotonic() - self.frames[0].received_at <= 0.25:
            return tuple(self.frames)
        return ()


class SDKPreview:
    """Native callbacks only copy into a bounded queue; worker owns SDK handles."""

    def __init__(self, device, stream):
        self.device = device
        self.stream = stream
        self.loop = asyncio.get_running_loop()
        self.created = time.monotonic()
        self.ready = asyncio.Event()
        self.profile = None
        self.consumers = set()
        self.references = 0
        self.error = None
        self.gop = GOPCache()
        self._raw = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._overflow = threading.Event()
        self._keyframe = threading.Event()
        self._using_es = threading.Event()
        self._output = deque(maxlen=64)
        self._output_lock = threading.Lock()
        self._scheduled = False
        self._thread = threading.Thread(target=self._run, name=f"hikvision-preview-{stream.id}", daemon=True)
        self._real_callback = RealDataCallback(self._on_real)
        self._es_callback = ESCallback(self._on_es)
        self._thread.start()

    def _put(self, item):
        if self._stop.is_set():
            return
        try:
            self._raw.put_nowait(item)
        except queue.Full:
            self._overflow.set()

    def _on_real(self, handle, kind, pointer, size, user):
        if self._using_es.is_set():
            return
        if kind in (SYSHEAD, STREAMDATA) and pointer and 0 < size <= MAX_BUFFER:
            self._put(("ps", string_at(pointer, size), 0, 0))

    def _on_es(self, handle, info, user):
        if not info:
            return
        packet = info.contents
        if packet.dwPacketMode != 0:
            self._put(("fallback", b"", 0, 0))
            return
        if not packet.pPacketBuffer or not 0 < packet.dwPacketSize <= MAX_BUFFER:
            return
        kind = packet.dwPacketType
        if kind in (0, 1, 2, 3, 10):
            self._put(
                (
                    "es",
                    string_at(packet.pPacketBuffer, packet.dwPacketSize),
                    ((packet.dwTimeStampHigh << 32) | packet.dwTimeStamp) * 90,
                    kind,
                )
            )

    def _publish(self, media):
        with self._output_lock:
            if len(self._output) == self._output.maxlen:
                self._output.clear()
                self._output.append(None)
                self._keyframe.set()
            self._output.append(media)
            if self._scheduled:
                return
            self._scheduled = True
        self.loop.call_soon_threadsafe(self._drain)

    def _drain(self):
        with self._output_lock:
            items = list(self._output)
            self._output.clear()
            self._scheduled = False
        for media in items:
            self.gop.push(media)
            if media and media.kind == "video" and media.keyframe:
                self.profile = media.profile
                self.ready.set()
            for consumer in tuple(self.consumers):
                consumer.push(media)

    def request_keyframe(self):
        """Safe to call on the event loop or the SDK worker."""
        self._keyframe.set()

    def _clear_raw(self):
        while True:
            try:
                self._raw.get_nowait()
            except queue.Empty:
                return

    def _run(self):
        sdk = self.device.sdk_subscription
        try:
            bind_preview(sdk)
        except (RuntimeError, AttributeError, TypeError) as err:
            self.error = str(err)
            self.loop.call_soon_threadsafe(self.ready.set)
            return
        channel = preview_channel(self.device, self.stream)
        stream_type = int(self.stream.id) % 100
        while not self._stop.is_set():
            handle = -1
            try:
                self._clear_raw()
                self._overflow.clear()
                self._using_es.clear()
                processor = MediaProcessor(
                    self.created, self.stream.audio_codec, self.stream.audio_sample_rate, self._publish
                )
                info = NET_DVR_PREVIEWINFO()
                info.lChannel = channel
                info.dwStreamType = stream_type - 1
                info.dwLinkMode = 0
                info.bBlocked = 0
                info.dwDisplayBufNum = 1
                handle = sdk.NET_DVR_RealPlay_V40(self.device.sdk_user, byref(info), self._real_callback, None)
                if handle < 0:
                    raise RuntimeError(f"SDK preview failed (code {sdk.NET_DVR_GetLastError()})")
                set_es = getattr(sdk, "NET_DVR_SetESRealPlayCallBack", None)
                es_mode = bool(set_es and set_es(handle, self._es_callback, None))
                if es_mode:
                    self._using_es.set()
                self._keyframe.set()
                last_video = time.monotonic()
                es_seen = False
                es_deadline = time.monotonic() + 1
                while not self._stop.is_set():
                    if es_mode and not es_seen and time.monotonic() > es_deadline:
                        es_mode = False
                        self._using_es.clear()
                        self._keyframe.set()
                    if time.monotonic() - last_video > 10:
                        raise RuntimeError("SDK preview stopped producing video")
                    if self._keyframe.is_set():
                        self._keyframe.clear()
                        name = "NET_DVR_MakeKeyFrameSub" if stream_type == 2 else "NET_DVR_MakeKeyFrame"
                        if stream_type in (1, 2) and (func := getattr(sdk, name, None)):
                            func(self.device.sdk_user, channel)
                    if self._overflow.is_set():
                        # Arbitrary PS chunks cannot be dropped and then parsed
                        # as a continuous stream. Restart at a fresh header/IDR.
                        raise RuntimeError("SDK preview queue overflow")
                    try:
                        mode, data, pts, packet_type = self._raw.get(timeout=0.25)
                    except queue.Empty:
                        if time.monotonic() - last_video > 10:
                            raise RuntimeError("SDK preview stopped producing video")
                        continue
                    if mode == "fallback" and es_mode:
                        es_mode = False
                        self._using_es.clear()
                        processor = MediaProcessor(
                            self.created, self.stream.audio_codec, self.stream.audio_sample_rate, self._publish
                        )
                        self._publish(None)
                        self._keyframe.set()
                    elif mode == "es" and es_mode:
                        if packet_type in (0, 1, 2, 3) and data.startswith((b"\x00\x00\x01", b"\x00\x00\x00\x01")):
                            es_seen = es_seen or packet_type in (1, 2, 3)
                            processor.video(data, pts)
                            last_video = time.monotonic()
                        elif packet_type == 10 and processor.audio_codec:
                            processor.audio(data, pts)
                    elif mode == "ps" and not es_mode and not data.startswith(b"IMKH"):
                        processor.program_stream(data)
                        # Watch valid video, not unrelated keepalive bytes.
                        if processor.ps_pts is not None and processor.ps_pts != getattr(
                            processor, "last_seen_pts", None
                        ):
                            processor.last_seen_pts = processor.ps_pts
                            last_video = time.monotonic()
            except Exception as err:  # noqa: BLE001 -- native/codec boundary must recover, not kill the worker
                _LOGGER.warning("SDK preview %s interrupted: %s; retrying", self.stream.id, err)
                self._publish(None)
            finally:
                if handle >= 0:
                    try:
                        sdk.NET_DVR_StopRealPlay(handle)
                    except Exception:
                        _LOGGER.exception("Failed to stop SDK preview %s", self.stream.id)
            self._stop.wait(1)

    async def close(self):
        """Stop native preview before its login handle can be logged out."""
        self._stop.set()
        await asyncio.to_thread(self._thread.join)
        self._clear_raw()
        self.consumers.clear()
        self.gop.clear()
