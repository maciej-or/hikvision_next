"""Tests for the incremental multipart stream parser used by the arming connection."""

from custom_components.hikvision_next.isapi.multipart_stream import (
    MultipartStreamParser,
    extract_boundary,
)


def _part(boundary: str, headers: str, body: bytes) -> bytes:
    return f"--{boundary}\r\n{headers}\r\n\r\n".encode() + body + b"\r\n"


def test_extract_boundary_plain():
    assert extract_boundary('multipart/mixed; boundary=frontier') == "frontier"


def test_extract_boundary_quoted():
    assert extract_boundary('multipart/form-data; boundary="AaB03x"') == "AaB03x"


def test_extract_boundary_missing():
    assert extract_boundary("application/json") is None


def test_single_part_with_content_length_one_chunk():
    parser = MultipartStreamParser("frontier")
    body = b'{"eventType": "heartBeat"}'
    stream = _part("frontier", f"Content-Type: application/json\r\nContent-Length: {len(body)}", body)

    parts = parser.feed(stream)

    assert len(parts) == 1
    headers, received_body = parts[0]
    assert headers["content-type"] == "application/json"
    assert received_body == body


def test_part_split_across_multiple_chunks():
    parser = MultipartStreamParser("frontier")
    body = b'{"eventType": "cidEvent"}'
    stream = _part("frontier", f"Content-Type: application/json\r\nContent-Length: {len(body)}", body)

    # Feed one byte at a time to exercise buffering across boundary/header/body splits.
    parts = []
    for i in range(len(stream)):
        parts.extend(parser.feed(stream[i : i + 1]))

    assert len(parts) == 1
    assert parts[0][1] == body


def test_multiple_parts_in_sequence():
    parser = MultipartStreamParser("frontier")
    body1 = b'{"eventType": "heartBeat"}'
    body2 = b'{"eventType": "cidEvent"}'
    stream = _part("frontier", f"Content-Type: application/json\r\nContent-Length: {len(body1)}", body1) + _part(
        "frontier", f"Content-Type: application/json\r\nContent-Length: {len(body2)}", body2
    )

    parts = parser.feed(stream)

    assert [b for _, b in parts] == [body1, body2]


def test_part_without_content_length_falls_back_to_boundary_scan():
    parser = MultipartStreamParser("frontier")
    body = b"<EventNotificationAlert>heartbeat</EventNotificationAlert>"
    # No Content-Length header at all -- must be delimited by the next boundary.
    stream = b"--frontier\r\nContent-Type: application/xml\r\n\r\n" + body + b"\r\n--frontier--"

    parts = parser.feed(stream)

    assert len(parts) == 1
    assert parts[0][1] == body


def test_empty_stream_yields_no_parts():
    parser = MultipartStreamParser("frontier")
    assert parser.feed(b"") == []
