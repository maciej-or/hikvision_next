"""Image entities with camera snapshots."""

from datetime import datetime
import logging

import voluptuous as vol

from homeassistant.components.camera import Camera
from homeassistant.components.image import ENTITY_ID_FORMAT, ImageEntity
from homeassistant.const import ATTR_ENTITY_ID, CONF_FILENAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.template import Template
from homeassistant.util import slugify

from . import HikvisionConfigEntry
from .const import (
    ACTION_UPDATE_SNAPSHOT,
    ANPR_IMAGE_SUFFIX,
    CONF_CONNECTION_SDK,
    DOMAIN,
    FACE_SNAP_EVENT_IDS,
    FACE_VERIFY_IMAGE_SUFFIX,
    resolve_connection_type,
)
from .hikvision_device import HikvisionDevice
from .isapi import CameraStreamInfo, EventInfo

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: HikvisionConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add images with snapshots."""

    device = entry.runtime_data

    entities = []
    seen_anpr_image_ids: set[str] = set()

    def add_anpr_snap(event: EventInfo, device_id: int, camera_name: str) -> None:
        if resolve_connection_type(entry.data) != CONF_CONNECTION_SDK:
            return
        if event.id != "anpr" or not event.anpr_image_unique_id:
            return
        if event.anpr_image_unique_id in seen_anpr_image_ids:
            return
        seen_anpr_image_ids.add(event.anpr_image_unique_id)
        anpr_snap = AnprSnapImage(hass, device, event, device_id, camera_name)
        entities.append(anpr_snap)
        hass.data.setdefault(DOMAIN, {}).setdefault("anpr_snap_images", {})[
            anpr_snap.unique_id
        ] = anpr_snap

    for camera in device.cameras:
        for stream in camera.streams:
            if stream.type_id == 1:
                entities.append(SnapshotFile(hass, device, camera, stream))

        if (
            resolve_connection_type(entry.data) == CONF_CONNECTION_SDK
            and any(
                event.id in FACE_SNAP_EVENT_IDS and event.channel_id == camera.id
                for event in device.supported_events
            )
        ):
            face_snap = FaceSnapImage(hass, device, camera)
            entities.append(face_snap)
            hass.data.setdefault(DOMAIN, {}).setdefault("face_snap_images", {})[
                face_snap.unique_id
            ] = face_snap

        for event in camera.events_info:
            add_anpr_snap(event, camera.id, camera.name)

    for event in device.events_info:
        camera_name = device.device_info.name
        if len(device.cameras) == 1:
            camera_name = device.cameras[0].name
        add_anpr_snap(event, 0, camera_name)

    if (
        resolve_connection_type(entry.data) == CONF_CONNECTION_SDK
        and any(event.id == "face" for event in device.events_info)
    ):
        face_verify = FaceVerifyImage(hass, device)
        entities.append(face_verify)
        hass.data.setdefault(DOMAIN, {}).setdefault("face_verify_images", {})[
            face_verify.unique_id
        ] = face_verify

    async_add_entities(entities)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        ACTION_UPDATE_SNAPSHOT,
        {vol.Required(CONF_FILENAME): cv.template},
        "update_snapshot_filename",
    )


class SnapshotFile(ImageEntity):
    """An entity for displaying snapshot files."""

    _attr_has_entity_name = True
    file_path = None

    def __init__(
        self,
        hass: HomeAssistant,
        device: HikvisionDevice,
        camera: Camera,
        stream_info: CameraStreamInfo,
    ) -> None:
        """Initialize the snapshot file."""

        ImageEntity.__init__(self, hass)

        self._attr_unique_id = slugify(f"{device.device_info.serial_no.lower()}_{stream_info.id}_snapshot")
        self.entity_id = ENTITY_ID_FORMAT.format(self.unique_id)
        self._attr_translation_key = "snapshot"
        self._attr_translation_placeholders = {"camera": camera.name}

    def image(self) -> bytes | None:
        """Return bytes of image."""
        try:
            if self.file_path:
                with open(self.file_path, "rb") as file:
                    return file.read()
        except FileNotFoundError:
            _LOGGER.warning(
                "Could not read camera %s image from file: %s",
                self.name,
                self.file_path,
            )
        return None

    async def update_snapshot_filename(
        self,
        filename: Template,
    ) -> None:
        """Update the file_path."""
        self.file_path = filename.async_render(variables={ATTR_ENTITY_ID: self.entity_id})
        self._attr_image_last_updated = datetime.now()
        self.schedule_update_ha_state()


class FaceSnapImage(ImageEntity):
    """Latest face snap image received from the SDK alarm callback."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        device: HikvisionDevice,
        camera: Camera,
    ) -> None:
        """Initialize the face snap image entity."""
        ImageEntity.__init__(self, hass)

        serial = device.device_info.serial_no.lower()
        self._attr_unique_id = slugify(f"{serial}_{camera.id}_face_snap")
        self.entity_id = ENTITY_ID_FORMAT.format(self.unique_id)
        self._attr_translation_key = "face_snap"
        self._attr_translation_placeholders = {"camera": camera.name}
        self._attr_device_info = device.hass_device_info(camera.id)
        self._image_bytes: bytes | None = None

    def image(self) -> bytes | None:
        """Return bytes of the latest face snap image."""
        return self._image_bytes

    def update_from_snap(self, image_bytes: bytes, attributes: dict | None = None) -> None:
        """Store a new face snap image and refresh the entity state."""
        self._image_bytes = image_bytes
        self._attr_image_last_updated = datetime.now()
        if attributes:
            self._attr_extra_state_attributes = {
                **getattr(self, "_attr_extra_state_attributes", {}),
                **attributes,
            }
        self.schedule_update_ha_state()


class FaceVerifyImage(ImageEntity):
    """Latest face verification image from an ACS door station SDK alarm."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        device: HikvisionDevice,
    ) -> None:
        """Initialize the ACS face verification image entity."""
        ImageEntity.__init__(self, hass)

        serial = device.device_info.serial_no.lower()
        self._attr_unique_id = slugify(f"{serial}_{FACE_VERIFY_IMAGE_SUFFIX}")
        self.entity_id = ENTITY_ID_FORMAT.format(self._attr_unique_id)
        self._attr_translation_key = "face_verify"
        self._attr_device_info = device.hass_device_info(0)
        self._image_bytes: bytes | None = None

    def image(self) -> bytes | None:
        """Return bytes of the latest face verification image."""
        return self._image_bytes

    def update_from_snap(self, image_bytes: bytes, attributes: dict | None = None) -> None:
        """Store a new face verification image and refresh the entity state."""
        self._image_bytes = image_bytes
        self._attr_image_last_updated = datetime.now()
        if attributes:
            self._attr_extra_state_attributes = {
                **getattr(self, "_attr_extra_state_attributes", {}),
                **attributes,
            }
        self.schedule_update_ha_state()


class AnprSnapImage(ImageEntity):
    """Latest ANPR detection image received from the SDK alarm callback."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        device: HikvisionDevice,
        event: EventInfo,
        device_id: int,
        camera_name: str,
    ) -> None:
        """Initialize the ANPR snap image entity."""
        ImageEntity.__init__(self, hass)

        self._attr_unique_id = slugify(
            event.anpr_image_unique_id or f"{event.unique_id}_{ANPR_IMAGE_SUFFIX}"
        )
        self.entity_id = ENTITY_ID_FORMAT.format(self._attr_unique_id)
        self._attr_translation_key = "anpr_snap"
        self._attr_translation_placeholders = {"camera": camera_name}
        self._attr_device_info = device.hass_device_info(device_id)
        self._attr_entity_registry_enabled_default = not event.disabled
        self._image_bytes: bytes | None = None

    def image(self) -> bytes | None:
        """Return bytes of the latest ANPR detection image."""
        return self._image_bytes

    def update_from_snap(self, image_bytes: bytes, attributes: dict | None = None) -> None:
        """Store a new ANPR image and refresh the entity state."""
        self._image_bytes = image_bytes
        self._attr_image_last_updated = datetime.now()
        if attributes:
            self._attr_extra_state_attributes = {
                **getattr(self, "_attr_extra_state_attributes", {}),
                **attributes,
            }
        self.schedule_update_ha_state()