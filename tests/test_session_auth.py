"""Tests for the ISAPI "Security/sessionLogin" authentication fallback."""

import hashlib

import httpx
import respx

from custom_components.hikvision_next.isapi.isapi import ISAPIClient
from custom_components.hikvision_next.isapi.session_auth import (
    SessionLoginAuth,
    SessionLoginCapabilities,
    _encode_password,
    _parse_session_capabilities,
    _sha256_hex,
)

TEST_HOST = "http://1.0.0.255"


def test_sha256_hex_matches_hashlib():
    assert _sha256_hex("hello") == hashlib.sha256(b"hello").hexdigest()


def test_sha256_hex_normalizes_windows_line_endings():
    # The reference implementation normalizes "\r\n" to "\n" before hashing (matching how the
    # device's own web UI does it) -- without this, a value containing a CRLF would hash
    # differently than what the device computed on its side.
    assert _sha256_hex("a\r\nb") == hashlib.sha256(b"a\nb").hexdigest()


def test_encode_password_reversible():
    """isIrreversible=false: sha256(password) + challenge, then (iterations - 1) more rounds."""
    cap = SessionLoginCapabilities(
        session_id="1",
        session_id_version=None,
        challenge="chal",
        salt=None,
        salt2=None,
        is_irreversible=False,
        iterations=3,
    )

    expected = f"{hashlib.sha256(b'secret').hexdigest()}chal"
    expected = hashlib.sha256(expected.encode()).hexdigest()
    expected = hashlib.sha256(expected.encode()).hexdigest()

    assert _encode_password("admin", "secret", cap) == expected


def test_encode_password_irreversible_v2():
    """sessionIDVersion "2" + isIrreversible: sha256(user+salt+pwd), then +challenge, then
    (iterations - 2) more rounds. No salt2 involved for this version.
    """
    cap = SessionLoginCapabilities(
        session_id="1",
        session_id_version="2",
        challenge="chal",
        salt="s1",
        salt2="unused",
        is_irreversible=True,
        iterations=3,
    )

    expected = hashlib.sha256(b"admins1secret").hexdigest()
    expected = hashlib.sha256(f"{expected}chal".encode()).hexdigest()
    expected = hashlib.sha256(expected.encode()).hexdigest()

    assert _encode_password("admin", "secret", cap) == expected


def test_encode_password_irreversible_v1():
    """Older sessionIDVersion + isIrreversible: an extra round mixing in salt2."""
    cap = SessionLoginCapabilities(
        session_id="1",
        session_id_version="1",
        challenge="chal",
        salt="s1",
        salt2="s2",
        is_irreversible=True,
        iterations=2,
    )

    expected = hashlib.sha256(b"admins1secret").hexdigest()
    expected = hashlib.sha256(f"admins2{expected}".encode()).hexdigest()
    expected = hashlib.sha256(f"{expected}chal".encode()).hexdigest()

    assert _encode_password("admin", "secret", cap) == expected


def test_parse_session_capabilities():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <SessionLoginCap version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">
        <sessionID>abc123</sessionID>
        <sessionIDVersion>2</sessionIDVersion>
        <challenge>thechallenge</challenge>
        <isIrreversible>true</isIrreversible>
        <salt>thesalt</salt>
        <iterations>500</iterations>
    </SessionLoginCap>"""

    cap = _parse_session_capabilities(xml)

    assert cap.session_id == "abc123"
    assert cap.session_id_version == "2"
    assert cap.challenge == "thechallenge"
    assert cap.is_irreversible is True
    assert cap.salt == "thesalt"
    assert cap.salt2 is None
    assert cap.iterations == 500


def test_parse_session_capabilities_invalid_returns_none():
    assert _parse_session_capabilities("<NotWhatWeExpect/>") is None


@respx.mock
async def test_detect_auth_method_uses_digest_when_advertised():
    """Plain WWW-Authenticate detection, unchanged from before sessionLogin existed -- no
    password sent, no sessionLogin probe involved at all at this stage.
    """
    digest_header = 'Digest realm="testrealm", qop="auth", nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093", opaque="799d5"'

    route = respx.get(f"{TEST_HOST}/ISAPI/System/deviceInfo").respond(
        status_code=401, headers={"WWW-Authenticate": digest_header}
    )

    isapi = ISAPIClient(TEST_HOST, "admin", "secret")
    await isapi._detect_auth_method()

    assert isinstance(isapi._auth_method, httpx.DigestAuth)
    assert route.call_count == 1
    assert "Authorization" not in route.calls[0].request.headers


@respx.mock
async def test_detect_auth_method_caches_noop_auth_when_no_challenge():
    """A bare 200 (no WWW-Authenticate challenge) must be cached, not left undetected --
    otherwise every single request() call re-probes forever. Confirmed against real
    hardware: once a sessionLogin cookie has been issued, httpx's own cookie jar (shared by
    self._session) keeps attaching it automatically even to this auth-less probe, so the
    device answers 200 here without this code ever setting a Cookie header itself.
    """
    route = respx.get(f"{TEST_HOST}/ISAPI/System/deviceInfo").respond(status_code=200, text="<DeviceInfo/>")

    isapi = ISAPIClient(TEST_HOST, "admin", "secret")
    await isapi._detect_auth_method()

    assert isinstance(isapi._auth_method, httpx.Auth)
    assert not isinstance(isapi._auth_method, (httpx.BasicAuth, httpx.DigestAuth, SessionLoginAuth))

    # request() must not re-probe now that an auth method (even a no-op one) is cached.
    result = await isapi.request("GET", "System/deviceInfo", present="text")
    assert result == "<DeviceInfo/>"
    assert route.call_count == 2  # one from _detect_auth_method above, one from request()


@respx.mock
async def test_request_keeps_digest_when_it_works():
    """A device where the configured password is accepted under Digest never touches
    sessionLogin at all -- this is the common case (an NVR, in testing) and must incur zero
    extra requests compared to before sessionLogin existed.
    """
    digest_header = 'Digest realm="testrealm", qop="auth", nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093", opaque="799d5"'

    def respond(request: httpx.Request) -> httpx.Response:
        if "Authorization" in request.headers:
            return httpx.Response(200, text="<DeviceInfo/>")
        return httpx.Response(401, headers={"WWW-Authenticate": digest_header})

    respx.get(f"{TEST_HOST}/ISAPI/System/deviceInfo").mock(side_effect=respond)
    session_login_route = respx.post(url__regex=r".*sessionLogin.*").mock(return_value=httpx.Response(400))

    isapi = ISAPIClient(TEST_HOST, "admin", "secret")
    result = await isapi.request("GET", "System/deviceInfo", present="text")

    assert result == "<DeviceInfo/>"
    assert isinstance(isapi._auth_method, httpx.DigestAuth)
    assert not session_login_route.called


@respx.mock
async def test_request_falls_back_to_session_login_after_real_401():
    """If Digest is advertised but the device rejects the real request outright (e.g. an AX
    Hybrid PRO panel added to a Hik-Connect account), fall back to sessionLogin and retry --
    reactively, only once a real request actually fails, never as an upfront probe.
    """
    digest_header = 'Digest realm="testrealm", qop="auth", nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093", opaque="799d5"'
    capabilities_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <SessionLoginCap xmlns="http://www.isapi.org/ver20/XMLSchema">
        <sessionID>sess-1</sessionID>
        <sessionIDVersion>2</sessionIDVersion>
        <challenge>chal</challenge>
        <isIrreversible>true</isIrreversible>
        <salt>salty</salt>
        <iterations>2</iterations>
    </SessionLoginCap>"""

    def respond(request: httpx.Request) -> httpx.Response:
        # Digest is never accepted (simulates the panel rejecting the plain password
        # outright); only a request carrying the sessionLogin cookie succeeds.
        if "Cookie" in request.headers:
            return httpx.Response(200, text="<DeviceInfo/>")
        return httpx.Response(401, headers={"WWW-Authenticate": digest_header})

    respx.get(f"{TEST_HOST}/ISAPI/System/deviceInfo").mock(side_effect=respond)
    respx.get(url__regex=r".*sessionLogin/capabilities.*").mock(return_value=httpx.Response(200, text=capabilities_xml))
    respx.post(url__regex=r".*sessionLogin.*").mock(
        return_value=httpx.Response(200, headers={"Set-Cookie": "WebSession=xyz; Path=/"})
    )

    isapi = ISAPIClient(TEST_HOST, "admin", "secret")
    result = await isapi.request("GET", "System/deviceInfo", present="text")

    assert result == "<DeviceInfo/>"
    assert isinstance(isapi._auth_method, SessionLoginAuth)


@respx.mock
async def test_session_login_auth_flow_full_handshake():
    """End-to-end: 401 on the real request -> fetch capabilities -> POST login -> cookie
    attached and the original request retried.
    """
    capabilities_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <SessionLoginCap xmlns="http://www.isapi.org/ver20/XMLSchema">
        <sessionID>sess-1</sessionID>
        <sessionIDVersion>2</sessionIDVersion>
        <challenge>chal</challenge>
        <isIrreversible>true</isIrreversible>
        <salt>salty</salt>
        <iterations>2</iterations>
    </SessionLoginCap>"""

    respx.get(f"{TEST_HOST}/ISAPI/SomeEndpoint").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, text="ok")]
    )
    respx.get(url__regex=r".*sessionLogin/capabilities.*").mock(return_value=httpx.Response(200, text=capabilities_xml))
    respx.post(url__regex=r".*sessionLogin.*").mock(
        return_value=httpx.Response(200, headers={"Set-Cookie": "WebSession=xyz; Path=/"})
    )

    auth = SessionLoginAuth("admin", "secret")
    async with httpx.AsyncClient(base_url=TEST_HOST) as client:
        response = await client.get("/ISAPI/SomeEndpoint", auth=auth)

    assert response.status_code == 200
    assert auth.cookie == "WebSession=xyz"
