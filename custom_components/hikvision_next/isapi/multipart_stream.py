"""Incremental parser for a never-ending multipart HTTP stream.

Used for the ISAPI "arming" connection (Event/notification/alertStream), where the device
keeps a single HTTP response open indefinitely and pushes one multipart part per event as it
occurs. Unlike a regular multipart body, the stream never ends with a closing boundary, so it
can't be parsed with a whole-body decoder (e.g. requests_toolbelt) -- parts must be extracted
as bytes arrive.
"""

from __future__ import annotations


class MultipartStreamParser:
    """Extracts complete (headers, body) parts from a boundary-delimited byte stream."""

    def __init__(self, boundary: str) -> None:
        """Initialize."""
        self._delimiter = f"--{boundary}".encode()
        self._buffer = bytearray()
        self._headers: dict[str, str] | None = None
        self._body_length: int | None = None

    def feed(self, chunk: bytes) -> list[tuple[dict[str, str], bytes]]:
        """Feed newly received bytes and return any parts that are now fully available."""
        self._buffer.extend(chunk)
        parts: list[tuple[dict[str, str], bytes]] = []

        while True:
            if self._headers is None and not self._consume_part_headers():
                break

            body = self._consume_part_body()
            if body is None:
                break

            parts.append((self._headers, body))
            self._headers = None
            self._body_length = None

        return parts

    def _consume_part_headers(self) -> bool:
        """Find the next boundary and parse the headers that follow it."""
        idx = self._buffer.find(self._delimiter)
        if idx == -1:
            return False

        after = idx + len(self._delimiter)
        if self._buffer[after : after + 2] == b"--":
            # Closing delimiter -- the device isn't expected to send one on this
            # connection, but drop it defensively so we don't loop forever on it.
            del self._buffer[: after + 2]
            return False

        # skip the CRLF that ends the boundary line itself, if present
        if self._buffer[after : after + 2] == b"\r\n":
            after += 2

        header_end = self._buffer.find(b"\r\n\r\n", after)
        if header_end == -1:
            return False

        header_block = bytes(self._buffer[after:header_end]).decode("utf-8", errors="replace")
        headers: dict[str, str] = {}
        for line in header_block.split("\r\n"):
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()

        del self._buffer[: header_end + 4]
        self._headers = headers
        content_length = headers.get("content-length")
        self._body_length = int(content_length) if content_length and content_length.isdigit() else None
        return True

    def _consume_part_body(self) -> bytes | None:
        """Extract the current part's body, if enough of it has arrived."""
        if self._body_length is not None:
            if len(self._buffer) < self._body_length:
                return None
            body = bytes(self._buffer[: self._body_length])
            del self._buffer[: self._body_length]
            if self._buffer[:2] == b"\r\n":
                del self._buffer[:2]
            return body

        # No Content-Length header: fall back to scanning for the next boundary.
        idx = self._buffer.find(self._delimiter)
        if idx == -1:
            return None
        body = bytes(self._buffer[:idx]).rstrip(b"\r\n")
        del self._buffer[:idx]
        return body


def extract_boundary(content_type: str) -> str | None:
    """Extract the multipart boundary value from a Content-Type header."""
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("boundary="):
            return part[len("boundary=") :].strip('"')
    return None
