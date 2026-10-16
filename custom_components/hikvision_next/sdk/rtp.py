"""Per-connection adapters for the pinned aiortc 1.14 RTP sender.

Keep large compressed IDRs from monopolizing the event loop / UDP socket.
Private sender fields are deliberately isolated here; never patch aiortc globals.
"""

import asyncio
from collections import OrderedDict
from types import MethodType

from aiortc.rtp import wrap_rtx

HISTORY_PACKETS = 2048
HISTORY_BYTES = 8 * 1024 * 1024
BURST_PACKETS = 16
BURST_INTERVAL = 0.001


class PacketHistory:
    """Bound both packet count and bytes, indexed by the full 16-bit sequence."""

    def __init__(self):
        self.packets = OrderedDict()
        self.size = 0

    def __setitem__(self, unused_ring_index, packet):
        # aiortc writes sequence % 128; retain the actual sequence instead.
        old = self.packets.pop(packet.sequence_number, None)
        if old:
            self.size -= len(old.payload)
        self.packets[packet.sequence_number] = packet
        self.size += len(packet.payload)
        while len(self.packets) > HISTORY_PACKETS or self.size > HISTORY_BYTES:
            _, old = self.packets.popitem(last=False)
            self.size -= len(old.payload)

    def get(self, sequence):
        return self.packets.get(sequence)


async def _retransmit(sender, sequence_number):
    packet = sender._RTCRtpSender__rtp_history.get(sequence_number)
    if packet is None:
        sender._hikvision_nack_misses += 1
        return
    sender._hikvision_retransmits += 1
    if sender._RTCRtpSender__rtx_payload_type is not None:
        packet = wrap_rtx(
            packet,
            payload_type=sender._RTCRtpSender__rtx_payload_type,
            sequence_number=sender._RTCRtpSender__rtx_sequence_number,
            ssrc=sender._rtx_ssrc,
        )
        sender._RTCRtpSender__rtx_sequence_number = (sender._RTCRtpSender__rtx_sequence_number + 1) & 0xFFFF
    await sender.transport._send_rtp(packet.serialize(sender._RTCRtpSender__rtp_header_extensions_map))


def configure_video_sender(sender):
    """Install bounded NACK history and cooperative pacing on this PC only."""
    sender._RTCRtpSender__rtp_history = PacketHistory()
    sender._hikvision_retransmits = 0
    sender._hikvision_nack_misses = 0
    sender._retransmit = MethodType(_retransmit, sender)
    transport = sender.transport
    if not hasattr(transport, "_hikvision_video_ssrcs"):
        transport._hikvision_video_ssrcs = set()
        transport._hikvision_pacing_yields = 0
        original_send = transport._send_rtp
        count = 0

        async def send(data):
            nonlocal count
            # RTCP and audio bypass pacing even on a bundled transport.
            video = (
                len(data) >= 12
                and not 192 <= data[1] <= 223
                and int.from_bytes(data[8:12], "big") in transport._hikvision_video_ssrcs
            )
            if video:
                count += 1
                if count >= BURST_PACKETS:
                    count = 0
                    transport._hikvision_pacing_yields += 1
                    await asyncio.sleep(BURST_INTERVAL)
            await original_send(data)

        transport._send_rtp = send
    transport._hikvision_video_ssrcs.update((sender._ssrc, sender._rtx_ssrc))
