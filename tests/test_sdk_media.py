"""SDK media regression cases independent of hardware."""

import time
from ctypes import sizeof
from fractions import Fraction
from itertools import pairwise

import av
import pytest

from custom_components.hikvision_next.sdk.media import MediaProcessor, PsDemuxer, TimestampMapper, nal_units
from custom_components.hikvision_next.sdk.preview import NET_DVR_PREVIEWINFO, PacketInfo, preview_channel


@pytest.fixture
def video_packets():
    encoder = av.CodecContext.create("libx264", "w")
    encoder.width, encoder.height = 64, 48
    encoder.pix_fmt = "yuv420p"
    encoder.time_base = Fraction(1, 25)
    encoder.framerate = Fraction(25)
    encoder.options = {"preset": "ultrafast", "tune": "zerolatency", "profile": "baseline", "g": "5", "bf": "0"}
    packets = []
    for index in range(12):
        frame = av.VideoFrame(64, 48, "yuv420p")
        for plane in frame.planes:
            plane.update(bytes([index * 10]) * plane.buffer_size)
        frame.pts = index
        packets.extend(encoder.encode(frame))
    return packets


def pes(data, pts=None, sid=0xE0):
    header = b"\x80\x00\x00"
    if pts is not None:
        stamp = bytes(
            (
                0x21 | ((pts >> 29) & 14),
                (pts >> 22) & 255,
                ((pts >> 14) & 254) | 1,
                (pts >> 7) & 255,
                ((pts << 1) & 254) | 1,
            )
        )
        header = b"\x80\x80\x05" + stamp
    size = len(header) + len(data)
    return b"\x00\x00\x01" + bytes([sid]) + size.to_bytes(2, "big") + header + data


def test_preview_abi():
    assert sizeof(NET_DVR_PREVIEWINFO) == 280
    assert NET_DVR_PREVIEWINFO.hPlayWnd.offset == 12
    assert NET_DVR_PREVIEWINFO.dwDisplayBufNum.offset == 60
    assert sizeof(PacketInfo) == 120
    assert PacketInfo.pPacketBuffer.offset == 64


@pytest.mark.parametrize("analog,logical,sdk_channel", [(0, 1, 33), (0, 33, 65), (16, 17, 33), (32, 33, 33)])
def test_nvr_logical_channel_mapping(analog, logical, sdk_channel):
    from types import SimpleNamespace

    from custom_components.hikvision_next.isapi.models import IPCamera

    camera = IPCamera(id=logical, name="test", model="", serial_no="", input_port=1, connection_type="proxied")
    device = SimpleNamespace(
        sdk_device_info=SimpleNamespace(byStartDChan=33),
        device_info=SimpleNamespace(is_nvr=True),
        cameras=[camera],
        capabilities=SimpleNamespace(analog_cameras_inputs=analog),
    )
    assert preview_channel(device, SimpleNamespace(id=logical * 100 + 2)) == sdk_channel


@pytest.mark.parametrize("value,rate", [("8", 8000), ("16kHz", 16000), (44100, 44100), (None, 8000), ("bad", 8000)])
def test_audio_sample_rate_metadata(value, rate):
    from custom_components.hikvision_next.isapi.models import parse_audio_sample_rate

    assert parse_audio_sample_rate(value) == rate


def test_ps_every_byte_boundary_and_unbounded_pes():
    data = pes(b"\x00\x00\x01\x65video", 90000) + pes(b"audio", 90090, 0xC0)
    parser = PsDemuxer()
    out = []
    for value in data:
        out.extend(parser.feed(bytes([value])))
    assert out == [("video", b"\x00\x00\x01\x65video", 90000, None), ("audio", b"audio", 90090, None)]
    unbounded = bytearray(pes(b"\x00\x00\x01\x65payload", 0))
    unbounded[4:6] = b"\x00\x00"
    assert parser.feed(bytes(unbounded) + pes(b"next", 3600))[0][1] == b"\x00\x00\x01\x65payload"


def test_video_passthrough_and_cached_parameter_sets(video_packets):
    out = []
    processor = MediaProcessor(time.monotonic(), "", 8000, out.append)
    for index, packet in enumerate(video_packets):
        processor.video(bytes(packet), index * 3600)
    assert len(out) == len(video_packets)
    assert out[0].keyframe
    assert out[1].payload == bytes(video_packets[1])  # no re-encoding
    assert out[1].pts - out[0].pts == 3600
    decoder = av.CodecContext.create("h264", "r")
    frames = []
    for media in out:
        frames.extend(decoder.decode(av.Packet(media.payload)))
    assert len(frames) == len(out)
    assert (frames[0].width, frames[0].height) == (64, 48)
    idr_only = b"\x00\x00\x01" + next(u for u in nal_units(bytes(video_packets[0])) if u[0] & 31 == 5)
    processor.video(idr_only, 90000)
    assert any(u[0] & 31 == 7 for u in nal_units(out[-1].payload))
    assert any(u[0] & 31 == 8 for u in nal_units(out[-1].payload))


def test_ps_reassembles_access_units(video_packets):
    out = []
    processor = MediaProcessor(time.monotonic(), "", 8000, out.append)
    stream = b"".join(pes(bytes(p), index * 3600) for index, p in enumerate(video_packets))
    for index in range(0, len(stream), 17):
        processor.program_stream(stream[index : index + 17])
    # A PS access unit is complete once the following PTS arrives.
    assert len(out) == len(video_packets) - 1
    assert out[0].keyframe
    assert out[1].payload == bytes(video_packets[1])


@pytest.mark.parametrize("codec,byte", [("G.711alaw", 0xD5), ("G.711ulaw", 0xFF)])
def test_g711_audio_clock_and_opus(codec, byte):
    from aiortc.codecs.opus import OpusEncoder

    out = []
    processor = MediaProcessor(time.monotonic(), codec, 8000, out.append)
    processor.audio(bytes([byte]) * 160, 90000)
    processor.audio(bytes([byte]) * 160, 91800)
    assert len(out) == 2
    assert out[1].pts - out[0].pts == 1800
    assert out[0].payload.samples == 160
    encoder = OpusEncoder()
    payloads = []
    for media in out:
        data, _ = encoder.encode(media.payload)
        payloads.extend(data)
    assert payloads


def test_aac_adts_split_frames():
    encoder = av.CodecContext.create("aac", "w")
    encoder.sample_rate, encoder.layout = 16000, "mono"
    encoder.format = "fltp"
    encoder.time_base = Fraction(1, 16000)
    packets = []
    for index in range(4):
        frame = av.AudioFrame(format="fltp", layout="mono", samples=1024)
        frame.sample_rate, frame.pts = 16000, index * 1024
        frame.planes[0].update(bytes(frame.planes[0].buffer_size))
        packets.extend(encoder.encode(frame))
    packets.extend(encoder.encode(None))
    adts = bytearray()
    for packet in packets:
        size = len(bytes(packet)) + 7
        # AAC-LC, 16kHz (sampling_frequency_index=8), mono.
        adts.extend(
            bytes([0xFF, 0xF1, 0x60, 0x40 | (size >> 11), (size >> 3) & 255, ((size & 7) << 5) | 31, 0xFC])
            + bytes(packet)
        )
    out = []
    processor = MediaProcessor(time.monotonic(), "AAC", 16000, out.append)
    processor.audio(bytes(adts[:5]), 0)
    for index in range(5, len(adts), 31):
        processor.audio(bytes(adts[index : index + 31]), None)
    assert out
    assert all(m.payload.sample_rate == 16000 for m in out)
    assert all(b.pts > a.pts for a, b in pairwise(out))


def test_timestamp_wrap_and_av_offset():
    clock = TimestampMapper(time.monotonic())
    first = clock.map((1 << 33) - 900)
    assert clock.map(900) - first == 1800
    assert clock.map(1800) - clock.map(900) == 900
