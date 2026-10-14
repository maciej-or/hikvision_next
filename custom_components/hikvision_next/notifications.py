"""Events listener."""

from __future__ import annotations

from http import HTTPStatus
import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

from aiohttp import web
from requests_toolbelt.multipart import MultipartDecoder

from homeassistant.components.http import HomeAssistantView
from homeassistant.const import CONTENT_TYPE_TEXT_PLAIN, STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_registry import async_get
from homeassistant.util import slugify

from .const import ALARM_SERVER_PATH, DOMAIN, HIKVISION_EVENT, SECURITY_COORDINATOR
from .hikvision_device import HikvisionDevice
from .isapi import AlertInfo, IPCamera, ISAPIClient
from .isapi.const import EVENT_IO
from .isapi.utils import deep_get

_LOGGER = logging.getLogger(__name__)

CONTENT_TYPE = "Content-Type"
CONTENT_TYPE_XML = (
    "application/xml",
    'application/xml; charset="UTF-8"',
    "text/xml",
)
CONTENT_TYPE_TEXT_HTML = "text/html"
CONTENT_TYPE_IMAGE = "image/jpeg"


def _normalize_serial(value: str | None) -> str:
    """Strip non-alphanumeric characters for tolerant serial number comparison."""
    return re.sub(r"[^A-Za-z0-9]", "", value or "").upper()


class EventNotificationsView(HomeAssistantView):
    """Event notifications listener."""

    def __init__(self, hass: HomeAssistant):
        """Initialize."""
        self.requires_auth = False
        self.url = ALARM_SERVER_PATH
        self.name = DOMAIN
        self.device: HikvisionDevice
        self.hass = hass

    async def post(self, request: web.Request):
        """Accept the POST request from NVR or IP Camera."""

        try:
            _LOGGER.debug("--- Incoming event notification ---")
            _LOGGER.debug("Source: %s", request.remote)
            xml = await self.parse_event_request(request)
            _LOGGER.debug("alert info: %s", xml)
            raw = ISAPIClient.parse_event_notification_raw(xml)
            serial_no = deep_get(raw, "Extensions.serialNumber.#text")
            self.device = self.get_isapi_device(request.remote, raw.get("macAddress"), serial_no)

            if self.device.device_info.is_security_panel:
                self.handle_security_cp_event(raw)
            else:
                alert = ISAPIClient.parse_event_notification(xml)
                self.update_alert_channel(alert)
                self.trigger_sensor(alert)
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.warning("Cannot process incoming event %s", ex)

        response = web.Response(status=HTTPStatus.OK, content_type=CONTENT_TYPE_TEXT_PLAIN)
        return response

    def get_isapi_device(self, device_ip, mac: str | None, serial_no: str | None = None) -> HikvisionDevice:
        """Get integration instance for device sending alert."""
        # Only consider entries that have actually finished their own setup. A config entry
        # that's mid-(re)load (e.g. another device reconnecting/reloading at the same moment)
        # hasn't reached "entry.runtime_data = device" yet, and accessing runtime_data on it
        # raises AttributeError -- which previously crashed this whole lookup and dropped the
        # incoming event for every device, not just the one that was reloading.
        integration_entries = [
            item
            for item in self.hass.config_entries.async_entries(DOMAIN)
            if not item.disabled_by and getattr(item, "runtime_data", None) is not None
        ]
        instance_identifiers = []
        entry = None
        if len(integration_entries) == 1:
            entry = integration_entries[0]
        else:
            # Search device by mac_address
            for item in integration_entries:
                item_mac_address = item.runtime_data.device_info.mac_address
                instance_identifiers.append(item_mac_address)

                if item_mac_address == mac:
                    entry = item
                    break

            # Search device by serial number. Not every event includes a macAddress, and the
            # source IP can't be trusted behind NAT/port-forwarding (e.g. a Docker host publishing
            # port 8123 typically reports the container gateway as the peer address, not the real
            # device IP) -- the serial number is a stable identifier unaffected by either. Compare
            # normalized (alphanumeric-only) values: some NVR firmware reports its own serial
            # slightly differently in event notifications than in System/deviceInfo (e.g. missing
            # a hyphen), so an exact match would silently fail.
            if not entry and serial_no:
                for item in integration_entries:
                    if _normalize_serial(item.runtime_data.device_info.serial_no) == _normalize_serial(serial_no):
                        entry = item
                        break

            # Search device by ip_address
            if not entry:
                for item in integration_entries:
                    url = item.runtime_data.host
                    instance_identifiers.append(url)

                    if self.get_ip(urlparse(url).hostname) == device_ip:
                        entry = item
                        break

        if not entry:
            raise ValueError(f"Cannot find ISAPI instance for device {device_ip} in {instance_identifiers}")

        return entry.runtime_data

    def get_ip(self, ip_string: str) -> str:
        """Return an IP if either hostname or IP is provided."""

        try:
            ipaddress.ip_address(ip_string)
            return ip_string
        except ValueError:
            resolved_hostname = socket.gethostbyname(ip_string)
            _LOGGER.debug("Resolve host %s resolves to IP %s", ip_string, resolved_hostname)

            return resolved_hostname

    async def parse_event_request(self, request: web.Request) -> str:
        """Extract XML content from multipart request or from simple request."""

        data = await request.read()

        content_type_header = request.headers.get(CONTENT_TYPE).strip()

        _LOGGER.debug("request headers: %s", request.headers)
        xml = None
        if content_type_header in CONTENT_TYPE_XML:
            xml = data.decode("utf-8")
        else:
            # "multipart/form-data; boundary=boundary"
            decoder = MultipartDecoder(data, content_type_header)
            for part in decoder.parts:
                headers = {}
                for key, value in part.headers.items():
                    assert isinstance(key, bytes)
                    headers[key.decode("ascii")] = value.decode("ascii")
                _LOGGER.debug("part headers: %s", headers)
                if headers.get(CONTENT_TYPE) in CONTENT_TYPE_XML:
                    xml = part.text
                if headers.get(CONTENT_TYPE) == CONTENT_TYPE_IMAGE:
                    _LOGGER.debug("image found")
                    # Use camera.snapshot service instead
                    # from datetime import datetime
                    # import aiofiles
                    # now = datetime.now()
                    # filename = f"/media/{DOMAIN}/snapshots/{now.strftime('%Y-%m-%d_%H-%M-%S_%f')}.jpg"
                    # async with aiofiles.open(filename, "wb") as image_file:
                    #     await image_file.write(part.content)
                    #     await image_file.flush()

        if not xml:
            raise ValueError(f"Unexpected event Content-Type {content_type_header}")
        return xml

    def update_alert_channel(self, alert: AlertInfo) -> AlertInfo:
        """Fix channel id for NVR/DVR alert."""

        if alert.channel_id > 32:
            # channel id above 32 is an IP camera
            # On DVRs that support analog cameras 33 may not be
            # camera 1 but camera 5 for example
            try:
                alert.channel_id = [
                    camera.id
                    for camera in self.device.cameras
                    if isinstance(camera, IPCamera) and camera.input_port == alert.channel_id - 32
                ][0]
            except IndexError:
                alert.channel_id = alert.channel_id - 32

    def trigger_sensor(self, alert: AlertInfo) -> None:
        """Determine entity and set binary sensor state."""

        _LOGGER.debug("Alert: %s", alert)

        serial_no = self.device.device_info.serial_no.lower()

        device_id_param = f"_{alert.channel_id}" if alert.channel_id != 0 and alert.event_id != EVENT_IO else ""
        io_port_id_param = f"_{alert.io_port_id}" if alert.io_port_id != 0 else ""
        unique_id = f"binary_sensor.{slugify(serial_no)}{device_id_param}{io_port_id_param}_{alert.event_id}"

        _LOGGER.debug("UNIQUE_ID: %s", unique_id)

        entity_registry = async_get(self.hass)
        entity_id = entity_registry.async_get_entity_id(Platform.BINARY_SENSOR, DOMAIN, unique_id)
        if entity_id:
            entity = self.hass.states.get(entity_id)
            if entity:
                attributes = dict(entity.attributes) if entity else {}
                if hasattr(alert, "target_type") and alert.target_type:
                  attributes["target_type"] = alert.target_type
                self.hass.states.async_set(entity_id, STATE_ON, attributes)
                self.fire_hass_event(alert)
            return
        raise ValueError(f"Entity not found {entity_id}")

    def fire_hass_event(self, alert: AlertInfo):
        """Fire HASS event."""
        camera_name = ""
        if camera := self.device.get_camera_by_id(alert.channel_id):
            camera_name = camera.name

        message = {
            "channel_id": alert.channel_id,
            "io_port_id": alert.io_port_id,
            "camera_name": camera_name,
            "event_id": alert.event_id,
        }
        if alert.detection_target:
            message["detection_target"] = alert.detection_target
            message["region_id"] = alert.region_id
        if alert.target_type:
            message["target_type"] = alert.target_type

        self.hass.bus.fire(
            HIKVISION_EVENT,
            message,
        )

    def handle_security_cp_event(self, raw: dict) -> None:
        """Handle a push event from a security control panel (SecurityCP).

        The exact payload schema for zone/partition events is not documented by
        Hikvision (only the generic transport is), so instead of guessing field
        names we log the raw payload for later calibration and trigger an
        immediate coordinator refresh, so entity state reflects the change
        within one HTTP round-trip rather than waiting for the next poll.
        """
        event_type = raw.get("eventType")
        event_state = raw.get("eventState")
        _LOGGER.info(
            "Security control panel event from %s (eventType=%s, eventState=%s): %s",
            self.device.host,
            event_type,
            event_state,
            raw,
        )

        coordinator = self.device.coordinators.get(SECURITY_COORDINATOR)
        if coordinator:
            self.hass.async_create_task(coordinator.async_request_refresh())

        self.hass.bus.fire(
            HIKVISION_EVENT,
            {"event_type": event_type, "event_state": event_state},
        )
