"""Events listener."""

from __future__ import annotations

from dataclasses import replace
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

from .const import (
    ALARM_SERVER_PATH,
    ANPR_LICENSE_PLATE_SENSOR_SUFFIX,
    DEVICE_LEVEL_EVENT_IDS,
    DOMAIN,
    HIKVISION_EVENT,
    TEXT_SENSOR_EVENT_IDS,
)
from .hikvision_device import HikvisionDevice
from .isapi import AlertInfo, IPCamera, ISAPIClient
from .isapi.const import EVENT_IO

import json

_LOGGER = logging.getLogger(__name__)

CONTENT_TYPE = "Content-Type"
CONTENT_TYPE_XML = (
    "application/xml",
    'application/xml; charset="UTF-8"',
    "text/xml",
)
CONTENT_TYPE_TEXT_HTML = "text/html"
CONTENT_TYPE_IMAGE = "image/jpeg"
CONTENT_TYPE_JSON = "application/json"


class EventNotificationsView(HomeAssistantView):
    """Event notifications listener."""

    def __init__(self, hass: HomeAssistant):
        """Initialize."""
        self.requires_auth = False
        self.url = ALARM_SERVER_PATH
        self.name = DOMAIN
        self.device: HikvisionDevice
        self.hass = hass

    async def update_face_verify_image(
        self,
        device: HikvisionDevice,
        image_bytes: bytes,
        *,
        person_name: str | None = None,
        employee_no: str | None = None,
        card_no: str | None = None,
        pic_len: int | None = None,
    ) -> None:
        """Update the ACS face verification image entity for a door station."""
        from .const import FACE_VERIFY_IMAGE_SUFFIX

        self.device = device
        serial = device.device_info.serial_no.lower()
        unique_id = slugify(f"{serial}_{FACE_VERIFY_IMAGE_SUFFIX}")
        entity = self.hass.data.get(DOMAIN, {}).get("face_verify_images", {}).get(unique_id)
        if entity is None:
            _LOGGER.debug("No face verify image entity registered for %s", unique_id)
            return

        attributes: dict[str, int | str] = {}
        if person_name:
            attributes["name"] = person_name
        if employee_no:
            attributes["employee_no"] = employee_no
        if card_no:
            attributes["card_no"] = card_no
        if pic_len is not None:
            attributes["pic_data_len"] = pic_len
        entity.update_from_snap(image_bytes, attributes or None)

    async def update_face_snap_image(
        self,
        device: HikvisionDevice,
        channel_id: int,
        image_bytes: bytes,
        *,
        face_pic_id: int | None = None,
        face_score: int | None = None,
    ) -> None:
        """Update the face snap image entity for a channel."""
        self.device = device
        channel_id = self._normalize_channel_id(device, channel_id)
        serial = device.device_info.serial_no.lower()
        unique_id = slugify(f"{serial}_{channel_id}_face_snap")
        entity = self.hass.data.get(DOMAIN, {}).get("face_snap_images", {}).get(unique_id)
        if entity is None:
            _LOGGER.debug("No face snap image entity registered for %s", unique_id)
            return

        attributes: dict[str, int] = {}
        if face_pic_id is not None:
            attributes["face_pic_id"] = face_pic_id
        if face_score is not None:
            attributes["face_score"] = face_score
        entity.update_from_snap(image_bytes, attributes or None)

    async def update_anpr_snap_image(
        self,
        device: HikvisionDevice,
        channel_id: int,
        image_bytes: bytes,
        *,
        license_plate: str | None = None,
        scene_len: int | None = None,
        plate_len: int | None = None,
    ) -> None:
        """Update the ANPR snap image entity for a channel."""
        self.device = device
        alert = AlertInfo(channel_id=channel_id, io_port_id=0, event_id="anpr")
        alert = self.update_alert_channel(alert)
        entity = self._resolve_anpr_image_entity(alert)
        if entity is None:
            _LOGGER.debug(
                "No ANPR snap image entity registered for %s channel %s",
                device.device_info.serial_no,
                alert.channel_id,
            )
            return

        attributes: dict[str, int | str] = {}
        if license_plate:
            attributes["license_plate"] = license_plate
        if scene_len is not None:
            attributes["scene_image_len"] = scene_len
        if plate_len is not None:
            attributes["plate_image_len"] = plate_len
        entity.update_from_snap(image_bytes, attributes or None)
        _LOGGER.debug(
            "ANPR image update: %s (%s bytes, plate=%s)",
            entity.entity_id,
            len(image_bytes),
            license_plate,
        )

    def _normalize_channel_id(
        self, device: HikvisionDevice, channel_id: int, event_id: str | None = None
    ) -> int:
        """Map SDK/NVR channel numbers to integration camera ids."""
        if event_id in DEVICE_LEVEL_EVENT_IDS and not device.device_info.is_nvr:
            return 0

        if channel_id > 32:
            try:
                return [
                    camera.id
                    for camera in device.cameras
                    if isinstance(camera, IPCamera) and camera.input_port == channel_id - 32
                ][0]
            except IndexError:
                channel_id = channel_id - 32

        if channel_id == 0 and not device.device_info.is_nvr and len(device.cameras) == 1:
            return device.cameras[0].id

        return channel_id

    async def handle_subscribed_event(
        self,
        raw_event: str | dict,
        device: HikvisionDevice | None = None,
    ) -> None:
        try:
            alert = ISAPIClient.parse_event_notification(raw_event)
            if alert:
                if device is not None:
                    self.device = device
                elif self.device is None:
                    device_ip = None
                    if isinstance(raw_event, dict):
                        device_ip = raw_event.get("ipAddress")
                    self.device = self.get_isapi_device(device_ip, alert)
                self.update_alert_channel(alert)
                self.trigger_sensor(alert)
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.warning("Error processing subscribed event", exc_info=ex)

    async def post(self, request: web.Request):
        """Accept the POST request from NVR or IP Camera."""

        try:
            # _LOGGER.debug("--- Incoming event notification ---")
            # _LOGGER.debug("Source: %s", request.remote)
            alert = None
            xml = await self.parse_event_request(request)
            alert = ISAPIClient.parse_event_notification(xml)

            # remote = [request.remote]
            # if isinstance(xml, dict):
            #     if 'macAddress' in xml:
            #         remote.append(xml.get('macAddress'))
            #     if 'ipAddress' in xml:
            #         remote.append(xml.get('ipAddress'))
            #     if 'ipv6Address' in xml:
            #         remote.append(xml.get('ipv6Address'))

            if alert:
                self.device = self.get_isapi_device(request.remote, alert)
                self.update_alert_channel(alert)
                self.trigger_sensor(alert)
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.warning("Cannot process event %s", ex)

        response = web.Response(status=HTTPStatus.OK, content_type=CONTENT_TYPE_TEXT_PLAIN)
        return response

    def _iter_loaded_devices(self) -> list[HikvisionDevice]:
        """Yield runtime devices for loaded, enabled config entries."""
        devices: list[HikvisionDevice] = []
        for item in self.hass.config_entries.async_entries(DOMAIN):
            if item.disabled_by:
                continue
            device = getattr(item, "runtime_data", None)
            if device is not None:
                devices.append(device)
        return devices

    def _match_device_by_alert(
        self,
        devices: list[HikvisionDevice],
        alert: AlertInfo,
        device_ip: str | None,
    ) -> HikvisionDevice | None:
        if alert.mac:
            mac = alert.mac.lower()
            for device in devices:
                if device.device_info.mac_address.lower() == mac:
                    return device

        if alert.device_serial_no:
            serial = alert.device_serial_no.lower()
            for device in devices:
                if device.device_info.serial_no.lower() == serial:
                    return device

        if device_ip:
            try:
                resolved_ip = self.get_ip(device_ip)
            except OSError:
                resolved_ip = device_ip
            for device in devices:
                host_ip = urlparse(device.host).hostname
                if device.device_info.ip_address == resolved_ip or host_ip == resolved_ip:
                    return device

        return None

    def _match_device_from_sdk_ref(
        self,
        alert: AlertInfo,
        device_ip: str | None,
    ) -> HikvisionDevice | None:
        sdk_devices = self.hass.data.get(DOMAIN, {}).get("sdk_ref", [])
        if not sdk_devices:
            return None

        wrapped = list(sdk_devices)
        if len(wrapped) == 1:
            return wrapped[0]

        return self._match_device_by_alert(wrapped, alert, device_ip)

    def get_isapi_device(self, device_ip, alert: AlertInfo) -> HikvisionDevice:
        """Get integration instance for device sending alert."""
        devices = self._iter_loaded_devices()
        instance_identifiers = [device.device_info.serial_no for device in devices]

        if len(devices) == 1:
            return devices[0]

        matched = self._match_device_by_alert(devices, alert, device_ip)
        if matched is not None:
            return matched

        matched = self._match_device_from_sdk_ref(alert, device_ip)
        if matched is not None:
            return matched

        raise ValueError(
            f"Cannot find ISAPI instance for device {device_ip} in {instance_identifiers}"
        )

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

        # _LOGGER.debug("request headers: %s", request.headers)
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

                if headers.get(CONTENT_TYPE) is None:
                    try:
                        xml = json.loads(part.text)
                    except json.decoder.JSONDecodeError:
                        pass
                    continue
                if headers.get(CONTENT_TYPE) in CONTENT_TYPE_XML:
                    xml = part.text
                elif headers.get(CONTENT_TYPE) in CONTENT_TYPE_JSON:
                    xml = json.loads(part.text)
                elif headers.get(CONTENT_TYPE) == CONTENT_TYPE_IMAGE:
                    _LOGGER.debug("image found")
                    # Use camera.snapshot service instead
                    # from datetime import datetime
                    # import aiofiles
                    # now = datetime.now()
                    # filename = f"/media/{DOMAIN}/snapshots/{now.strftime('%Y-%m-%d_%H-%M-%S_%f')}.jpg"
                    # async with aiofiles.open(filename, "wb") as image_file:
                    #     await image_file.write(part.content)
                    #     await image_file.flush()
                else:
                    _LOGGER.debug("part headers: %s", headers)
        if not xml:
            raise ValueError(f"Unexpected event Content-Type {content_type_header}")
        return xml

    def update_alert_channel(self, alert: AlertInfo) -> AlertInfo:
        """Fix channel id for NVR/DVR alert."""
        alert.channel_id = self._normalize_channel_id(self.device, alert.channel_id, alert.event_id)
        return alert

    def _event_scope_channel_id(self, alert: AlertInfo) -> int:
        if alert.event_id in DEVICE_LEVEL_EVENT_IDS and not self.device.device_info.is_nvr:
            return 0
        return alert.channel_id

    def _event_unique_id_parts(self, alert: AlertInfo) -> tuple[str, str, str]:
        serial_no = slugify(self.device.device_info.serial_no.lower())
        channel_id = self._event_scope_channel_id(alert)
        device_id_param = f"_{channel_id}" if channel_id != 0 and alert.event_id != EVENT_IO else ""
        io_port_id_param = f"_{alert.io_port_id}" if alert.io_port_id != 0 else ""
        return serial_no, device_id_param, io_port_id_param

    def _lookup_event_entity(self, alert: AlertInfo) -> tuple[str | None, str]:
        serial_no, device_id_param, io_port_id_param = self._event_unique_id_parts(alert)
        event_suffix = f"{serial_no}{device_id_param}{io_port_id_param}_{alert.event_id}"
        binary_unique_id = f"binary_sensor.{event_suffix}"
        sensor_unique_id = f"sensor.{event_suffix}"

        entity_registry = async_get(self.hass)
        entity_id = entity_registry.async_get_entity_id(Platform.BINARY_SENSOR, DOMAIN, binary_unique_id)
        unique_id = binary_unique_id
        if not entity_id:
            entity_id = entity_registry.async_get_entity_id(Platform.SENSOR, DOMAIN, sensor_unique_id)
            if entity_id:
                unique_id = sensor_unique_id
        elif alert.event_id in TEXT_SENSOR_EVENT_IDS:
            sensor_entity_id = entity_registry.async_get_entity_id(
                Platform.SENSOR, DOMAIN, sensor_unique_id
            )
            if sensor_entity_id:
                entity_id = sensor_entity_id
                unique_id = sensor_unique_id
        if not entity_id:
            unique_id = (
                sensor_unique_id
                if alert.event_id in TEXT_SENSOR_EVENT_IDS
                else binary_unique_id
            )
        return entity_id, unique_id

    def _lookup_anpr_plate_entity(self, alert: AlertInfo) -> tuple[str | None, str]:
        serial_no, device_id_param, io_port_id_param = self._event_unique_id_parts(alert)
        unique_id = f"{serial_no}{device_id_param}{io_port_id_param}_{ANPR_LICENSE_PLATE_SENSOR_SUFFIX}"
        entity_registry = async_get(self.hass)
        entity_id = entity_registry.async_get_entity_id(Platform.SENSOR, DOMAIN, unique_id)
        return entity_id, unique_id

    def _lookup_anpr_image_unique_id(self, alert: AlertInfo) -> str:
        from .const import ANPR_IMAGE_SUFFIX

        serial_no, device_id_param, io_port_id_param = self._event_unique_id_parts(alert)
        return f"{serial_no}{device_id_param}{io_port_id_param}_{ANPR_IMAGE_SUFFIX}"

    def _resolve_anpr_image_entity(self, alert: AlertInfo):
        """Resolve the in-memory ANPR snap image entity for an alert."""
        unique_id = slugify(self._lookup_anpr_image_unique_id(alert))
        entity = self.hass.data.get(DOMAIN, {}).get("anpr_snap_images", {}).get(unique_id)
        if entity is not None:
            return entity

        candidates = [alert]
        if not self.device.device_info.is_nvr:
            candidates.extend(
                replace(alert, channel_id=camera.id) for camera in self.device.cameras
            )
        candidates.append(replace(alert, channel_id=0))

        for candidate in candidates:
            candidate = self.update_alert_channel(candidate)
            unique_id = slugify(self._lookup_anpr_image_unique_id(candidate))
            entity = self.hass.data.get(DOMAIN, {}).get("anpr_snap_images", {}).get(unique_id)
            if entity is not None:
                return entity
        return None

    def _resolve_anpr_entities(self, alert: AlertInfo) -> tuple[str | None, str | None, str]:
        """Resolve ANPR binary/plate entities, including per-camera fallbacks."""
        entity_id, unique_id = self._lookup_event_entity(alert)
        plate_entity_id, _ = self._lookup_anpr_plate_entity(alert)

        if entity_id and plate_entity_id:
            return entity_id, plate_entity_id, unique_id

        candidates = [alert]
        if not self.device.device_info.is_nvr:
            candidates.extend(
                replace(alert, channel_id=camera.id) for camera in self.device.cameras
            )

        for candidate in candidates:
            if not entity_id:
                entity_id, unique_id = self._lookup_event_entity(candidate)
            if not plate_entity_id:
                plate_entity_id, _ = self._lookup_anpr_plate_entity(candidate)
            if entity_id and plate_entity_id:
                break

        return entity_id, plate_entity_id, unique_id

    def _face_sensor_attributes(self, alert: AlertInfo) -> dict[str, str]:
        attrs: dict[str, str] = {}
        if alert.face_person_name:
            attrs["name"] = alert.face_person_name
        if alert.face_employee_no:
            attrs["employee_no"] = alert.face_employee_no
        if alert.face_card_no:
            attrs["card_no"] = alert.face_card_no
        return attrs

    def trigger_sensor(self, alert: AlertInfo) -> None:
        """Determine entity and set binary sensor state."""

        _LOGGER.debug("Alert: %s", alert)

        if alert.event_id == "anpr":
            entity_id, plate_entity_id, unique_id = self._resolve_anpr_entities(alert)
        else:
            entity_id, unique_id = self._lookup_event_entity(alert)
            plate_entity_id = None
            if (
                not entity_id
                and alert.event_id not in DEVICE_LEVEL_EVENT_IDS
                and alert.channel_id == 0
                and not self.device.device_info.is_nvr
                and len(self.device.cameras) == 1
            ):
                entity_id, unique_id = self._lookup_event_entity(
                    replace(alert, channel_id=self.device.cameras[0].id)
                )

        if alert.event_id == "face":
            person = alert.state or "unknown"
            attrs = self._face_sensor_attributes(alert)
            event_text = self.hass.data.get(DOMAIN, {}).get("event_text_sensors", {}).get(unique_id)
            if event_text is not None:
                event_text.set_person(person, attrs or None)
                _LOGGER.info("Face recognition update: %s -> %s", event_text.entity_id, person)
                self.fire_hass_event(alert)
                return
            if entity_id:
                self.hass.states.async_set(entity_id, person, attrs or None)
                _LOGGER.info("Face recognition update: %s -> %s", entity_id, person)
                self.fire_hass_event(alert)
                return
            _LOGGER.debug("Face sensor not found for alert %s (unique_id: %s)", alert, unique_id)
            return

        event_binary = self.hass.data.get(DOMAIN, {}).get("event_binary_sensors", {}).get(unique_id)
        if event_binary is not None:
            event_binary.set_active(alert.state == STATE_ON)
            if alert.event_id != "anpr":
                self.fire_hass_event(alert)
                return

        if entity_id:
            _LOGGER.debug("Entity ID: %s", entity_id)

            entity = self.hass.states.get(entity_id)
            if entity:
                attributes = dict(entity.attributes)
                if alert.anpr_license_plate:
                    attributes["license_plate"] = alert.anpr_license_plate
                if alert.anpr_direction:
                    attributes["direction"] = alert.anpr_direction
                if alert.anpr_confidence_level:
                    attributes["confidence_level"] = alert.anpr_confidence_level
                self.hass.states.async_set(entity_id, alert.state, attributes)
                self.fire_hass_event(alert)

        if plate_entity_id and alert.anpr_license_plate:
            plate_attrs = {}
            if alert.anpr_direction:
                plate_attrs["direction"] = alert.anpr_direction
            if alert.anpr_confidence_level:
                plate_attrs["confidence_level"] = alert.anpr_confidence_level
            _LOGGER.info(
                "ANPR plate update: %s -> %s",
                plate_entity_id,
                alert.anpr_license_plate,
            )
            self.hass.states.async_set(
                plate_entity_id,
                alert.anpr_license_plate,
                plate_attrs,
            )
        elif alert.event_id == "anpr" and alert.anpr_license_plate:
            _LOGGER.warning(
                "ANPR plate entity not found for %s (plate=%s)",
                self.device.device_info.serial_no,
                alert.anpr_license_plate,
            )
        if entity_id:
            return
        _LOGGER.debug("Entity not found for alert %s (unique_id: %s)", alert, unique_id)

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
        if alert.anpr_license_plate:
            message["license_plate"] = alert.anpr_license_plate
        if alert.anpr_direction:
            message["direction"] = alert.anpr_direction
        if alert.anpr_confidence_level:
            message["confidence_level"] = alert.anpr_confidence_level
        if alert.face_person_name:
            message["name"] = alert.face_person_name
        if alert.face_employee_no:
            message["employee_no"] = alert.face_employee_no
        if alert.face_card_no:
            message["card_no"] = alert.face_card_no

        self.hass.bus.fire(
            HIKVISION_EVENT,
            message,
        )
