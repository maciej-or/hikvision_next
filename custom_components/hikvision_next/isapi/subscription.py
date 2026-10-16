"""Long-lived ISAPI event subscription (subscribeEvent) using asyncio.Task.

This is used for event types that are not delivered via the normal HTTP
alarm server callback (e.g. ANPR on certain ITC devices).
The class is intentionally generic so it can be reused for other
future event types that require a persistent subscribeEvent connection.
"""

from __future__ import annotations

import asyncio
import logging
import re
import traceback
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import TYPE_CHECKING

import httpx
from requests_toolbelt.multipart import MultipartDecoder
import json

if TYPE_CHECKING:
    from .isapi import ISAPIClient


_LOGGER = logging.getLogger(__name__)


# Generic default (will be overridden by client.build_subscribe_event_xml when possible)
DEFAULT_SUBSCRIBE_EVENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<SubscribeEvent>
    <heartbeat>5</heartbeat>
    <channelMode>all</channelMode>
    <eventMode>all</eventMode>
    <level>middle</level>
</SubscribeEvent>
"""


class EventSubscription:
    """Manages a long-lived subscribeEvent connection in a background asyncio.Task."""

    def __init__(
        self,
        client: ISAPIClient,
        on_event: Callable[[str | dict], Awaitable[None]] | None = None,
    ) -> None:
        """Initialize.

        Args:
            client: The ISAPIClient (usually a HikvisionDevice).
            on_event: Async callback that will be called with raw event data
                      (XML string or parsed dict) when an event is received.
        """
        self.client = client
        self._on_event = on_event
        self._task: asyncio.Task[None] | None = None
        self._response: httpx.Response | None = None
        self._stopping = False
        self._reconnect_delay = 2.0

    async def start(
        self,
        subscription_xml: str | None = None,
        event_types: list[str] | None = None,
    ) -> None:
        """Subscribe and start the background listener task.

        This is now a generic multi-event subscriber.

        Args:
            subscription_xml: Raw XML to use (highest priority).
            event_types: List of event types to subscribe to (e.g. ["ANPR", "fielddetection"]).
                         If provided and no xml, the client will try to build a proper payload.
        """
        if self._task and not self._task.done():
            _LOGGER.debug("EventSubscription already running for %s", self.client.host)
            return

        self._stopping = False

        # Build payload (priority: explicit xml > client's smart builder > fallback)
        if subscription_xml:
            xml = subscription_xml
        elif hasattr(self.client, "build_subscribe_event_xml"):
            xml = self.client.build_subscribe_event_xml(event_types=event_types)
        else:
            xml = DEFAULT_SUBSCRIBE_EVENT_XML

        self.xml = xml

        try:
            self._response = await self.client.subscribe_events(xml)
            self._task = asyncio.create_task(
                self._run(), name=f"hikvision-subscription-{self.client.host}"
            )
            _LOGGER.info("EventSubscription started for %s (events=%s)", self.client.host, event_types or "default")
        except Exception as ex:
            error_msg = str(ex)
            if "disconnected without sending a response" in error_msg.lower() or "no response" in error_msg.lower():
                _LOGGER.warning(
                    "Failed to start EventSubscription for %s: Device closed the connection without a proper response. "
                    "This usually means the subscription request was rejected by the device "
                    "(e.g. ANPR not permitted with 'binary' pictureURLType, subStatusCode=403, or missing permissions). "
                    "Inspect the device's /Event/notification/subscribeEventCap response for supported <Event> types "
                    "and <pictureURLType> options, then adjust the subscription payload accordingly. "
                    "Original error: %s",
                    self.client.host, ex
                )
            else:
                _LOGGER.warning("Failed to start EventSubscription for %s: %s", self.client.host, ex)
            await self._cleanup()
            raise
            # Do not raise - caller decides if this is fatal based on capabilities

    async def stop(self) -> None:
        """Stop the listener task and release resources."""
        self._stopping = True
        await self._cleanup()
        _LOGGER.info("EventSubscription stopped for %s", self.client.host)

    async def _cleanup(self) -> None:
        """Cancel task and close the HTTP response if open."""
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._task = None

        if self._response:
            with suppress(Exception):
                await self._response.aclose()
        self._response = None

    async def _run(self) -> None:
        """Main reader loop. Reads the long-lived response stream.

        Supports both plain XML streaming and multipart/mixed (used by many
        devices when pushing ANPR events with pictures over subscribeEvent).
        """
        while not self._stopping:
            if not self._response:
                try:
                    self._response = await self.client.subscribe_events(self.xml)
                    _LOGGER.info("EventSubscription reconnected for %s", self.client.host)
                    continue
                except Exception as reconnect_ex:
                    _LOGGER.error(
                        "EventSubscription reconnect failed for %s: %s",
                        self.client.host,
                        reconnect_ex,
                    )
                    await asyncio.sleep(self._reconnect_delay * 2)
                    continue

            content_type = self._response.headers.get("content-type", "")
            is_multipart = "multipart" in content_type.lower()

            try:
                if is_multipart:
                    await self._run_multipart_stream(content_type)
                else:
                    # Fallback to line-based for simple XML streams
                    async for line in self._response.aiter_lines():
                        if self._stopping:
                            return
                        await self._process_simple_event(line)

            except asyncio.CancelledError:
                raise
            except Exception as ex:
                if self._stopping:
                    return
                await self._on_event(None)
                _LOGGER.warning(
                    "EventSubscription stream error on %s: %s (will reconnect in %ss)",
                    self.client.host,
                    ex,
                    self._reconnect_delay,
                )
                traceback.print_exception(ex)
                await self._cleanup()

                if not self._stopping:
                    await asyncio.sleep(self._reconnect_delay)
                    self._response = None
                    continue
                return

            finally:
                if not self._stopping:
                    await self._cleanup()

    async def _run_multipart_stream(self, content_type: str) -> None:
        """Handle streaming multipart/mixed response.

        Uses boundary-based splitting + MultipartDecoder on individual parts
        (as requested) to extract XML events and images.
        """
        boundary_match = re.search(r'boundary=([^;]+)', content_type, re.IGNORECASE)
        if not boundary_match:
            _LOGGER.warning("Multipart response without boundary, falling back")
            await self._run_raw_bytes_fallback()
            return

        boundary = boundary_match.group(1).strip().strip('"\'')
        if not boundary.startswith("--"):
            boundary = "--" + boundary

        boundary_bytes = boundary.encode("utf-8", errors="ignore")
        buffer = bytearray()
        initial_response_checked = False

        async for chunk in self._response.aiter_bytes():
            if self._stopping:
                return
            buffer.extend(chunk)

            # Check for SubscribeEventResponse failure in the initial data (only once)
            if not initial_response_checked and len(buffer) > 200:
                text_so_far = buffer.decode("utf-8", errors="ignore")
                if "</SubscribeEventResponse>" in text_so_far:
                    initial_response_checked = True
                    if "<SubscribeEventResponse" in text_so_far and "<FailedEventList>" in text_so_far:
                        _LOGGER.warning(
                            "Device rejected one or more events for %s:\n%s",
                            self.client.host, text_so_far.strip()
                        )
                        await self._cleanup()
                        return

            while True:
                # Robust boundary search: prefer "\r\n" + boundary (interior delimiter)
                search_delim = b"\r\n" + boundary_bytes
                idx = buffer.find(search_delim)

                if idx != -1:
                    # Found interior boundary. The part starts after "\r\n" + boundary
                    part_start = idx + len(search_delim)
                else:
                    # Try bare boundary (possible at the very beginning of the body)
                    idx = buffer.find(boundary_bytes)
                    if idx == -1:
                        break
                    part_start = idx + len(boundary_bytes)

                # Try to find the header end for this part
                header_end = buffer.find(b"\r\n\r\n", part_start)
                if header_end == -1:
                    header_end = buffer.find(b"\n\n", part_start)

                if header_end != -1:
                    # We have headers for this part. Check for Content-Length.
                    header_section = bytes(buffer[part_start:header_end])
                    headers = {}
                    for line in header_section.splitlines():
                        if b":" in line:
                            k, v = line.split(b":", 1)
                            headers[k.strip().lower()] = v.strip()

                    content_length = None
                    if b"content-length" in headers:
                        try:
                            content_length = int(headers[b"content-length"])
                        except (ValueError, TypeError):
                            content_length = None

                    # print(self.client.host, boundary_bytes, content_length, len(buffer))
                    # print(buffer)

                    if content_length is not None:
                        # We know the exact body size. Calculate required bytes.
                        body_start = header_end + 4 if buffer[header_end:header_end+4] == b"\r\n\r\n" else header_end + 2
                        required = body_start + content_length

                        if len(buffer) < required:
                            # Not enough data yet for this length-based part
                            break

                        # Extract exactly using Content-Length
                        raw_part = bytes(buffer[part_start:required])

                        # Clean separators
                        raw_part = raw_part.strip(b"\r\n \t")

                        if raw_part:
                            await self._process_multipart_part(raw_part, boundary)

                        buffer = buffer[required:]
                        continue  # Process next part in the same buffer

                # Fallback: pure boundary-based extraction (original behavior)
                next_search = b"\r\n" + boundary_bytes
                next_idx = buffer.find(next_search, part_start)

                if next_idx == -1:
                    next_idx = buffer.find(boundary_bytes, part_start)
                    if next_idx == -1:
                        break  # Need more data

                raw_part = bytes(buffer[part_start:next_idx])
                raw_part = raw_part.strip(b"\r\n \t")

                if raw_part:
                    await self._process_multipart_part(raw_part, boundary)

                buffer = buffer[next_idx:]

    async def _process_multipart_part(self, raw_part: bytes, boundary: str) -> None:
        """Parse one multipart part. Uses MultipartDecoder on the part when possible."""
        try:
            # Try to split headers and body manually first (more reliable for streaming)
            header_end = raw_part.find(b"\r\n\r\n")
            if header_end == -1:
                header_end = raw_part.find(b"\n\n")

            if header_end != -1:
                header_bytes = raw_part[:header_end]
                body = raw_part[header_end + 4:] if raw_part[header_end:header_end+4] == b"\r\n\r\n" else raw_part[header_end + 2:]

                # Extra aggressive strip on body — sometimes multipart boundaries or extra CRLF leak in
                body = body.strip(b"\r\n \t").strip(b"\r\n-----------------------")

                # Parse headers
                headers = {}
                for line in header_bytes.splitlines():
                    if b":" in line:
                        k, v = line.split(b":", 1)
                        headers[k.strip().lower().decode("ascii", errors="ignore")] = v.strip().decode("ascii", errors="ignore")

                ctype = headers.get("content-type", "")

                if "xml" in ctype.lower() or not ctype:
                    text = body.decode("utf-8", errors="ignore").strip()
                    await self._on_event(text)
                    return

                if "json" in ctype.lower():
                    text = body.decode("utf-8", errors="ignore").strip()
                    await self._on_event(json.loads(text))
                    return

                if "image" in ctype.lower():
                    _LOGGER.debug("subscribeEvent: received image part (%d bytes)", len(body))
                    return

            # Fallback: use MultipartDecoder on the raw part (as requested)
            # Wrap it as a single-part multipart
            # fake_body = boundary.encode() + b"\r\n" + raw_part + b"\r\n" + boundary.encode() + b"--\r\n"
            #
            # try:
            #     decoder = MultipartDecoder(fake_body, f"multipart/mixed; boundary={boundary.lstrip('-')}")
            #     for part in decoder.parts:
            #         part_ctype = ""
            #         for k, v in part.headers.items():
            #             if isinstance(k, bytes):
            #                 k = k.decode("ascii", errors="ignore")
            #             if k.lower() == "content-type":
            #                 part_ctype = (v.decode("ascii", errors="ignore") if isinstance(v, bytes) else v).lower()
            #                 break
            #
            #         if "xml" in part_ctype or not part_ctype:
            #             text = part.text.strip() if part.text else ""
            #             await self._on_event(text)
            #         elif "image" in part_ctype:
            #             _LOGGER.debug("subscribeEvent: image part via MultipartDecoder (%d bytes)", len(part.content))
            # except Exception as dec_ex:
            #     _LOGGER.debug("MultipartDecoder fallback failed on part: %s", dec_ex)

        except Exception as ex:
            _LOGGER.warning("Error processing multipart part from subscribeEvent: %s", ex)
            traceback.print_exception(ex)

    async def _run_raw_bytes_fallback(self) -> None:
        """Fallback raw byte reader when we can't determine multipart structure."""
        async for chunk in self._response.aiter_bytes():
            if self._stopping:
                return
            # Very basic fallback - try to decode and look for XML events
            try:
                text = chunk.decode("utf-8", errors="ignore")
                for line in text.splitlines():
                    await self._process_simple_event(line)
            except Exception:
                pass

    async def _process_simple_event(self, line: str) -> None:
        """Fallback simple line-based processing for non-multipart streams."""
        line = line.strip()
        if not line:
            return

        for alert_xml in line:
            lowered = alert_xml.lower()
            if "heartbeat" in lowered or "heartBeat" in alert_xml:
                continue
            if self._on_event and "<SubscribeEventResponse" not in alert_xml:
                try:
                    await self._on_event(alert_xml)
                except Exception as ex:
                    _LOGGER.warning("Error in EventSubscription on_event callback: %s", ex)


__all__ = ["EventSubscription", "DEFAULT_SUBSCRIBE_EVENT_XML"]
