"""Events listener"""

from __future__ import annotations

from http import HTTPStatus
import logging

from aiohttp import web
from requests_toolbelt.multipart import MultipartDecoder

from homeassistant import core
from homeassistant.components.http import HomeAssistantView
from homeassistant.const import CONTENT_TYPE_TEXT_PLAIN, STATE_ON
from homeassistant.helpers import device_registry as dr
from homeassistant.util import slugify

from .const import ALARM_SERVER_PATH, DOMAIN
from .isapi import ISAPI

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

    requires_auth = False
    url = ALARM_SERVER_PATH
    name = DOMAIN

    async def post(self, request: web.Request):
        """Accept the POST request from NVR or IP Camera"""

        try:
            _LOGGER.debug("--- Incoming event notification ---")
            xml = await parse_event_request(request)
            _LOGGER.debug("alert info: %s", xml)
            trigger_sensor(request.app["hass"], xml)
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.warning("Cannot process incoming event %s", ex)

        response = web.Response(
            status=HTTPStatus.OK, content_type=CONTENT_TYPE_TEXT_PLAIN
        )
        return response


def trigger_sensor(hass: core.HomeAssistant, xml: str) -> None:
    """Determine entity and set binary sensor state"""

    alert = ISAPI.parse_event_notification(xml)

    device_serial = alert.device_serial

    if not device_serial and alert.mac:
        # get device_serial by mac
        device_registry = dr.async_get(hass)
        hass_device = device_registry.async_get_device(
            set(),
            connections={(dr.CONNECTION_NETWORK_MAC, alert.mac)},
        )
        if hass_device:
            device_serial = list(hass_device.identifiers)[0][1]

    if not device_serial or alert.channel_id == 0:
        raise ValueError("Cannot determine entity")

    device_serial = slugify(device_serial.lower())
    entity_id = f"binary_sensor.{device_serial}_{alert.channel_id}_{alert.event_id}"
    entity = hass.states.get(entity_id)
    if entity:
        hass.states.async_set(entity_id, STATE_ON, entity.attributes)
    else:
        raise ValueError(f"Entity not found {entity_id}")


async def parse_event_request(request: web.Request) -> str:
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
