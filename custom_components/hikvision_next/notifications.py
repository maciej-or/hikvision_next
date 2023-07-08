"""Events listener"""

from __future__ import annotations

from http import HTTPStatus
import ipaddress
import logging
import socket
from urllib.parse import urlparse

from aiohttp import web
from requests_toolbelt.multipart import MultipartDecoder

from homeassistant.components.http import HomeAssistantView
from homeassistant.const import CONTENT_TYPE_TEXT_PLAIN, STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_registry import async_get
from homeassistant.util import slugify

from .const import ALARM_SERVER_PATH, DATA_ISAPI, DOMAIN, HIKVISION_EVENT
from .isapi import ISAPI, AlertInfo, IPCamera

_LOGGER = logging.getLogger(__name__)

CONTENT_TYPE = "Content-Type"
CONTENT_TYPE_XML = (
    "application/xml",
    'application/xml; charset="UTF-8"',
    "text/xml",
)
CONTENT_TYPE_TEXT_HTML = "text/html"
CONTENT_TYPE_IMAGE = "image/jpeg"


class EventNotificationsView(HomeAssistantView):
    """Event notifications listener."""

    def __init__(self, hass: HomeAssistant):
        self.requires_auth = False
        self.url = ALARM_SERVER_PATH
        self.name = DOMAIN
        self.isapi: ISAPI
        self.hass = hass

    async def post(self, request: web.Request):
        """Accept the POST request from NVR or IP Camera"""

        try:
            _LOGGER.debug("--- Incoming event notification ---")
            _LOGGER.debug("Source: %s", request.remote)
            self.isapi = self.get_isapi_instance(request.remote)
            xml = await self.parse_event_request(request)
            _LOGGER.debug("alert info: %s", xml)
            self.trigger_sensor(xml)
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.warning("Cannot process incoming event %s", ex)

        response = web.Response(status=HTTPStatus.OK, content_type=CONTENT_TYPE_TEXT_PLAIN)
        return response

    def get_isapi_instance(self, device_ip) -> ISAPI:
        """Get isapi instance for device sending alert"""
        # Get list of instances
        try:
            entry = [
                entry
                for entry in self.hass.config_entries.async_entries(DOMAIN)
                if not entry.disabled_by and self.get_ip(urlparse(entry.data.get("host")).hostname) == device_ip
            ][0]

            config = self.hass.data[DOMAIN][entry.entry_id]

            return config.get(DATA_ISAPI)

        except IndexError:
            return None

    def get_ip(self, ip_string: str) -> str:
        """Return an IP if either hostname or IP is provided"""

        try:
            ipaddress.ip_address(ip_string)
            return ip_string
        except ValueError:
            resolved_hostname = socket.gethostbyname(ip_string)
            _LOGGER.debug("Resolve host %s resolves to IP %s", ip_string, resolved_hostname)

            return resolved_hostname

    async def parse_event_request(self, request: web.Request) -> str:
        """Extract XML content from multipart request or from simple request"""

        data = await request.read()

        content_type_header = request.headers.get(CONTENT_TYPE)

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

        if not xml:
            raise ValueError(f"Unexpected event Content-Type {content_type_header}")
        return xml

    def get_alert_info(self, xml: str) -> AlertInfo:
        """Parse incoming EventNotificationAlert XML message."""

        alert = ISAPI.parse_event_notification(xml)

        if alert.channel_id > 32:
            # channel id above 32 is an IP camera
            # On DVRs that support analog cameras 33 may not be
            # camera 1 but camera 5 for example
            try:
                alert.channel_id = [
                    camera.id
                    for camera in self.isapi.cameras
                    if isinstance(camera, IPCamera) and camera.input_port == alert.channel_id - 32
                ][0]
            except IndexError:
                alert.channel_id = alert.channel_id - 32

        return alert

    def trigger_sensor(self, xml: str) -> None:
        """Determine entity and set binary sensor state"""

        alert = self.get_alert_info(xml)
        _LOGGER.debug("Alert: %s", alert)

        serial_no = self.isapi.device_info.serial_no.lower()

        device_id_param = f"_{alert.channel_id}" if alert.channel_id != 0 else ""
        io_port_id_param = f"_{alert.io_port_id}" if alert.io_port_id != 0 else ""
        unique_id = (
            f"binary_sensor.{slugify(serial_no)}{device_id_param}{io_port_id_param}_{alert.event_id}"
        )

        _LOGGER.debug("UNIQUE_ID: %s", unique_id)

        # unique_id = f"binary_sensor.{slugify(serial_no)}_{alert.channel_id}" f"_{alert.event_id}"
        entity_registry = async_get(self.hass)
        entity_id = entity_registry.async_get_entity_id(Platform.BINARY_SENSOR, DOMAIN, unique_id)
        if entity_id:
            entity = self.hass.states.get(entity_id)
            if entity:
                self.hass.states.async_set(entity_id, STATE_ON, entity.attributes)
                self.fire_hass_event(alert)
            return
        raise ValueError(f"Entity not found {entity_id}")

    def fire_hass_event(self, alert: AlertInfo):
        """Fire HASS event"""
        camera_name = ""
        if camera := self.isapi.get_camera_by_id(alert.channel_id):
            camera_name = camera.name

        message = {
            "channel_id": alert.channel_id,
            "io_port_id": alert.io_port_id,
            "camera_name": camera_name,
            "event_id": alert.event_id,
        }

        self.hass.bus.fire(
            HIKVISION_EVENT,
            message,
        )
