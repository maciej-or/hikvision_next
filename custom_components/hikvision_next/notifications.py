"""Events listener"""

from __future__ import annotations

import html
import logging
import xmltodict

from http import HTTPStatus
from aiohttp import web
from requests_toolbelt.multipart import MultipartDecoder

from homeassistant.components.http import HomeAssistantView
from homeassistant.const import CONTENT_TYPE_TEXT_PLAIN, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import slugify

from .const import (
    ALARM_SERVER_PATH,
    DOMAIN,
    EVENTS,
    EVENTS_ALTERNATE_ID,
    HIKVISION_EVENT,
)
from .isapi import AlertInfo

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
            xml = await self.parse_event_request(request)
            _LOGGER.debug("alert info: %s", xml)
            self.trigger_sensor(request.app["hass"], xml)
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.warning("Cannot process incoming event %s", ex)

        response = web.Response(
            status=HTTPStatus.OK, content_type=CONTENT_TYPE_TEXT_PLAIN
        )
        return response

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

    def parse_event_notification(self, xml: str) -> AlertInfo:
        """Parse incoming EventNotificationAlert XML message."""

        # Fix for some cameras sending non html encoded data
        data = xmltodict.parse(html.escape(xml))

        alert = data["EventNotificationAlert"]

        channel_id = int(alert.get("channelID", alert.get("dynChannelID", "0")))
        if channel_id > 32:
            # workaround for wrong channelId provided by NVR
            # model: DS-7608NXI-I2/8P/S, Firmware: V4.61.067 or V4.62.200
            channel_id = channel_id - 32

        event_id = alert.get("eventType")
        if not event_id or event_id == "duration":
            # <EventNotificationAlert version="2.0"
            event_id = alert["DurationList"]["Duration"]["relationEvent"]
        event_id = event_id.lower()
        # handle alternate event type
        if EVENTS_ALTERNATE_ID.get(event_id):
            event_id = EVENTS_ALTERNATE_ID[event_id]

        device_serial = None
        if alert.get("Extensions"):
            # <EventNotificationAlert version="1.0"
            device_serial = alert["Extensions"]["serialNumber"]["#text"]

        # <EventNotificationAlert version="2.0"
        mac = alert.get("macAddress")

        if not EVENTS[event_id]:
            raise ValueError(f"Unsupported event {event_id}")

        return AlertInfo(channel_id, event_id, device_serial, mac)

    def trigger_sensor(self, hass: HomeAssistant, xml: str) -> None:
        """Determine entity and set binary sensor state"""

        alert = self.parse_event_notification(xml)
        _LOGGER.debug(alert)
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

        entity_id = f"binary_sensor.{slugify(device_serial.lower())}_{alert.channel_id}_{alert.event_id}"
        entity = hass.states.get(entity_id)
        if entity:
            hass.states.async_set(entity_id, STATE_ON, entity.attributes)
            self.fire_hass_event(hass, device_serial, alert)
        else:
            raise ValueError(f"Entity not found {entity_id}")

    def fire_hass_event(
        self, hass: HomeAssistant, device_serial: str, alert: AlertInfo
    ):
        device_registry = dr.async_get(hass)
        if not alert.mac:
            # Must be analog camera
            device_serial = f"{device_serial}-VI{alert.channel_id}"

        device_serial = f"{device_serial}-VI{alert.channel_id}"
        hass_device = device_registry.async_get_device(
            identifiers={(DOMAIN, device_serial)},
            connections=None,
        )

        if hass_device:
            device_name = hass_device.name
        else:
            device_name = "Unknown"

        message = {
            "channel_id": alert.channel_id,
            "camera_name": device_name,
            "event_id": alert.event_id,
        }

        hass.bus.fire(
            HIKVISION_EVENT,
            message,
        )
