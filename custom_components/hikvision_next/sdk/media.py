"""Incremental SDK media parsing. All methods run on the preview worker.

Video stays compressed throughout: Annex-B access units become av.Packets.
Only audio is decoded (aiortc encodes the resulting AudioFrames as Opus).
"""

import re
import time
from dataclasses import dataclass, field
from fractions import Fraction

import av

TIME_BASE = Fraction(1, 90000)
MAX_BUFFER = 4 * 1024 * 1024
START_CODE = b"\x00\x00\x01"


def nal_units(data: bytes) -> list[bytes]:
    """Split Annex-B without copying start codes into the NAL payload."""
    return [part for part in re.split(b"\x00\x00\x00?\x01", data) if part]


def audio_codec(name: str) -> str | None:
    """ISAPI names are different from FFmpeg decoder names."""
    return {
        "g.711alaw": "pcm_alaw",
        "g711a": "pcm_alaw",
        "g.711a": "pcm_alaw",
        "g.711ulaw": "pcm_mulaw",
        "g711u": "pcm_mulaw",
        "g.711u": "pcm_mulaw",
        "aac": "aac",
        "aac-lc": "aac",
    }.get(name.lower().replace(" ", ""))


@dataclass(frozen=True)
class Media:
    """Immutable compressed video or decoded audio shared by viewers."""

    kind: str
    payload: bytes | av.AudioFrame
    pts: int
    keyframe: bool = False
    profile: str | None = None
    received_at: float = field(default_factory=time.monotonic)


class TimestampMapper:
    """Unwrap MPEG's 33-bit clock and share one origin between audio/video."""

    def __init__(self, created: float):
        self.created = created
        self.origin = None
        self.base = 0

    def map(self, pts: int) -> int:
        if self.origin is None:
            self.origin = pts
            self.base = int((time.monotonic() - self.created) * 90000)
        delta = (pts - self.origin + (1 << 32)) % (1 << 33) - (1 << 32)
        return self.base + delta


class PsDemuxer:
    """Incremental MPEG-2 PS/PES parser retaining source PTS.

    PSM audio types supplement the per-stream ISAPI audio codec. Missing PTS
    continues the current video access unit; no wall-clock timestamp guessing.
    """

    def __init__(self):
        self.buffer = bytearray()
        self.types: dict[int, int] = {}

    @staticmethod
    def _pts(data: bytes) -> int:
        return (data[0] & 14) << 29 | data[1] << 22 | (data[2] & 254) << 14 | data[3] << 7 | data[4] >> 1

    def feed(self, data: bytes) -> list[tuple[str, bytes, int | None, int | None]]:
        self.buffer.extend(data)
        if len(self.buffer) > MAX_BUFFER:
            self.buffer.clear()
            raise ValueError("SDK PS buffer exceeded limit")
        out = []
        buf = self.buffer
        while len(buf) >= 4:
            start = buf.find(START_CODE)
            if start < 0:
                del buf[:-2]
                break
            if start:
                del buf[:start]
            if len(buf) < 4:
                break
            sid = buf[3]
            if sid == 0xB9:
                del buf[:4]
                continue
            if sid == 0xBA:
                if len(buf) < 14:
                    break
                size = 14 + (buf[13] & 7) if buf[4] & 0xC0 == 0x40 else 12
            else:
                if len(buf) < 6:
                    break
                size = 6 + int.from_bytes(buf[4:6], "big")
                if size == 6 and 0xE0 <= sid <= 0xEF:
                    # An unbounded PES ends at the next PS packet, not at a NAL.
                    match = re.search(b"\x00\x00\x01[\xb9-\xff]", buf[6:])
                    if match is None:
                        break
                    size = 6 + match.start()
            if len(buf) < size:
                break
            packet = bytes(buf[:size])
            del buf[:size]
            if sid == 0xBC:
                self._psm(packet)
            elif 0xC0 <= sid <= 0xEF or sid == 0xBD:
                if len(packet) < 9 or packet[6] & 0xC0 != 0x80:
                    continue
                offset = 9 + packet[8]
                if offset > len(packet):
                    continue
                pts = self._pts(packet[9:14]) if packet[7] & 0x80 and packet[8] >= 5 else None
                kind = "video" if 0xE0 <= sid <= 0xEF else "audio"
                out.append((kind, packet[offset:], pts, self.types.get(sid)))
        return out

    def _psm(self, data: bytes):
        if len(data) < 12:
            return
        index = 10 + int.from_bytes(data[8:10], "big")
        if index + 2 > len(data):
            return
        end = min(index + 2 + int.from_bytes(data[index : index + 2], "big"), len(data) - 4)
        index += 2
        while index + 4 <= end:
            self.types[data[index + 1]] = data[index]
            index += 4 + int.from_bytes(data[index + 2 : index + 4], "big")


class MediaProcessor:
    """Parse ES or PS from one preview generation; never called concurrently."""

    def __init__(self, created: float, codec: str, sample_rate: int, emit):
        self.clock = TimestampMapper(created)
        self.audio_codec = audio_codec(codec)
        self.sample_rate = sample_rate
        self.emit = emit
        self.ps = PsDemuxer()
        self.sps = self.pps = b""
        self.decoder = None
        self.ps_video = bytearray()
        self.ps_pts = None
        self.audio_pts = None
        self.aac_buffer = bytearray()

    def video(self, data: bytes, pts: int):
        units = nal_units(data)
        for unit in units:
            if unit[0] & 31 == 7:
                self.sps = unit
            elif unit[0] & 31 == 8:
                self.pps = unit
        key = any(unit[0] & 31 == 5 for unit in units)
        if not any(unit[0] & 31 in (1, 5) for unit in units):
            return
        if key:
            if not self.sps or not self.pps:
                return
            data = START_CODE + self.sps + START_CODE + self.pps + data
        profile = self.sps[1:4].hex() if len(self.sps) >= 4 else None
        self.emit(Media("video", data, self.clock.map(pts), key, profile))

    def audio(self, data: bytes, pts: int | None, codec: str | None = None):
        codec = codec or self.audio_codec
        if not codec:
            return
        if self.decoder is None or self.decoder.name != codec:
            self.decoder = av.CodecContext.create(codec, "r")
            if codec.startswith("pcm_"):
                self.decoder.sample_rate = self.sample_rate
                self.decoder.layout = "mono"
        if pts is not None and not (codec == "aac" and self.aac_buffer):
            self.audio_pts = self.clock.map(pts)
        if self.audio_pts is None:
            return
        packets = []
        if codec == "aac":
            # ADTS frame lengths provide boundaries even across SDK callbacks.
            self.aac_buffer.extend(data)
            if len(self.aac_buffer) > MAX_BUFFER:
                raise ValueError("SDK AAC buffer exceeded limit")
            while len(self.aac_buffer) >= 7:
                buf = self.aac_buffer
                if buf[0] != 0xFF or buf[1] & 0xF6 != 0xF0:
                    del buf[0]
                    continue
                size = (buf[3] & 3) << 11 | buf[4] << 3 | buf[5] >> 5
                if size < 7:
                    del buf[0]
                    continue
                if len(buf) < size:
                    break
                packets.append(av.Packet(bytes(buf[:size])))
                del buf[:size]
        else:
            packets.append(av.Packet(data))
        for packet in packets:
            for frame in self.decoder.decode(packet):
                # AudioFrames are immutable after publication. Each consumer
                # clones before aiortc's encoder/resampler can change them.
                frame.pts = round(self.audio_pts * frame.sample_rate / 90000)
                frame.time_base = Fraction(1, frame.sample_rate)
                self.emit(Media("audio", frame, self.audio_pts))
                self.audio_pts += round(frame.samples * 90000 / frame.sample_rate)

    def program_stream(self, data: bytes):
        for kind, payload, pts, stream_type in self.ps.feed(data):
            if kind == "audio":
                codec = {0x0F: "aac", 0x90: "pcm_alaw", 0x91: "pcm_mulaw"}.get(stream_type)
                self.audio(payload, pts, codec)
            else:
                if stream_type not in (None, 0x1B):
                    raise ValueError("SDK returned a non-H.264 video stream")
                if pts is not None and pts != self.ps_pts:
                    if self.ps_video and self.ps_pts is not None:
                        self.video(bytes(self.ps_video), self.ps_pts)
                    self.ps_video.clear()
                    self.ps_pts = pts
                self.ps_video.extend(payload)
                if len(self.ps_video) > MAX_BUFFER:
                    raise ValueError("SDK video access unit exceeded limit")
