"""ISAPI "Security/sessionLogin" authentication.

Some devices stop accepting Basic/Digest auth with the plain configured password and start
requiring this challenge-response flow instead -- confirmed to happen with an AX Hybrid PRO
panel once it's been added to a Hik-Connect account. The password is combined with a
server-issued salt/challenge and hashed client-side (SHA-256, several rounds) before being
sent, and a session cookie is used for subsequent requests instead of an Authorization header.

This mechanism isn't described in Hikvision's own ISAPI reference docs bundled with this repo;
the endpoints, XML schema and hashing scheme below were taken from the reference
implementation at https://github.com/petrleocompel/hikaxpro (hikaxpro/src/hikaxpro.py,
src/helpers/sha256.py), which reverse-engineered them from the device's own web UI. That
project reimplements SHA-256 by hand (a straight port of the obfuscated JS the web UI uses);
here it's replaced with the standard library's hashlib, which produces byte-identical output
for the same input.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from urllib.parse import quote

import httpx
import xmltodict

_LOGGER = logging.getLogger(__name__)

XML_SCHEMA = "http://www.isapi.org/ver20/XMLSchema"
SESSION_CAPABILITIES_PATH = "ISAPI/Security/sessionLogin/capabilities"
SESSION_LOGIN_PATH = "ISAPI/Security/sessionLogin"


def _sha256_hex(text: str) -> str:
    """Hash text the same way the device's own web UI does before hashing.

    The "\\r\\n" -> "\\n" normalization matches the reference implementation; without it, a
    salt/challenge/password containing a Windows-style line ending would hash differently
    than what the device computed on its side.
    """
    normalized = text.replace("\r\n", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass
class SessionLoginCapabilities:
    """Parsed response of Security/sessionLogin/capabilities."""

    session_id: str
    session_id_version: str | None
    challenge: str
    salt: str | None
    salt2: str | None
    is_irreversible: bool
    iterations: int


def _parse_session_capabilities(xml: str) -> SessionLoginCapabilities | None:
    """Parse a SessionLoginCap XML response."""
    try:
        data = xmltodict.parse(xml).get("SessionLoginCap", {})
        return SessionLoginCapabilities(
            session_id=data["sessionID"],
            session_id_version=data.get("sessionIDVersion"),
            challenge=data["challenge"],
            salt=data.get("salt"),
            salt2=data.get("salt2"),
            is_irreversible=str(data.get("isIrreversible", "false")).lower() == "true",
            iterations=int(data.get("iterations", 1)),
        )
    except (KeyError, ValueError):
        return None


def _encode_password(username: str, password: str, cap: SessionLoginCapabilities) -> str:
    """Hash the password against the device-issued salt/challenge.

    Three variants depending on what the device reports, mirroring the reference
    implementation exactly: newer devices (sessionIDVersion "2") only use one salt; older
    ones mix in a second salt (salt2); devices with isIrreversible=false use a much weaker
    scheme (no salt at all, just the challenge) -- kept for completeness, but not expected on
    a panel that's actually enforcing the hardened login this module exists for.
    """
    if cap.session_id_version == "2" and cap.is_irreversible:
        result = _sha256_hex(f"{username}{cap.salt}{password}")
        result = _sha256_hex(f"{result}{cap.challenge}")
        for _ in range(2, cap.iterations):
            result = _sha256_hex(result)
    elif cap.is_irreversible:
        result = _sha256_hex(f"{username}{cap.salt}{password}")
        result = _sha256_hex(f"{username}{cap.salt2}{result}")
        result = _sha256_hex(f"{result}{cap.challenge}")
        for _ in range(2, cap.iterations):
            result = _sha256_hex(result)
    else:
        result = f"{_sha256_hex(password)}{cap.challenge}"
        for _ in range(1, cap.iterations):
            result = _sha256_hex(result)
    return result


def _build_session_login_xml(cap: SessionLoginCapabilities, username: str, encoded_password: str) -> bytes:
    """Build the SessionLogin request body."""
    xml = xmltodict.unparse(
        {
            "SessionLogin": {
                "@version": "2.0",
                "@xmlns": XML_SCHEMA,
                "sessionID": cap.session_id,
                "userName": username,
                "password": encoded_password,
                "sessionIDVersion": cap.session_id_version,
            }
        }
    )
    return xml.encode("utf-8")


class SessionLoginAuth(httpx.Auth):
    """httpx.Auth implementation for ISAPI "Security/sessionLogin".

    Used as a fallback when Basic/Digest is advertised by the device but the configured
    password is rejected (see ISAPIClient._detect_auth_method). Async-only: this integration
    always talks to devices through an httpx.AsyncClient.
    """

    def __init__(self, username: str, password: str, user_level: int = 1) -> None:
        """Initialize."""
        self.username = username
        self.password = password
        self.user_level = user_level
        self.cookie: str | None = None

    def sync_auth_flow(self, request: httpx.Request):
        raise RuntimeError("SessionLoginAuth only supports async requests")

    async def async_auth_flow(self, request: httpx.Request):
        """Attach the current session cookie, and (re)establish a session on a 401."""
        if self.cookie:
            request.headers["Cookie"] = self.cookie
        response = yield request

        if response.status_code != 401:
            return

        await response.aread()
        self.cookie = None

        base_url = httpx.URL(scheme=request.url.scheme, host=request.url.host, port=request.url.port)

        cap_request = httpx.Request(
            "GET",
            base_url.join(f"/{SESSION_CAPABILITIES_PATH}?username={quote(self.username)}"),
            headers={"X-Userlevel": str(self.user_level)},
        )
        cap_response = yield cap_request
        await cap_response.aread()
        if cap_response.status_code != 200:
            return

        cap = _parse_session_capabilities(cap_response.text)
        if cap is None:
            _LOGGER.debug("Device does not support sessionLogin, or returned an unexpected response")
            return

        encoded_password = _encode_password(self.username, self.password, cap)
        login_body = _build_session_login_xml(cap, self.username, encoded_password)
        timestamp = int(time.time())
        login_request = httpx.Request(
            "POST",
            base_url.join(f"/{SESSION_LOGIN_PATH}?timeStamp={timestamp}"),
            content=login_body,
            headers={"Content-Type": "application/xml"},
        )
        login_response = yield login_request
        await login_response.aread()
        if login_response.status_code != 200:
            return

        cookie = login_response.headers.get("set-cookie")
        if cookie:
            self.cookie = cookie.split(";")[0]
        else:
            # The response's root element name isn't documented/consistent -- grab whatever
            # it is and look for a "sessionID" child under it, the same way the reference
            # implementation does with a namespace-relative XPath.
            parsed = xmltodict.parse(login_response.text)
            root = next(iter(parsed.values()), {}) if parsed else {}
            session_id = root.get("sessionID") if isinstance(root, dict) else None
            if session_id:
                self.cookie = f"WebSession={session_id}"

        if self.cookie:
            _LOGGER.info("Authenticated to %s via sessionLogin", request.url.host)
            request.headers["Cookie"] = self.cookie
            yield request
