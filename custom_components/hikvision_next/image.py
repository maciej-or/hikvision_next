"""Image entities with camera snapshots."""

from datetime import datetime
import logging

import voluptuous as vol

from homeassistant.components.camera import Camera
from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, CONF_FILENAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.template import Template
from homeassistant.util import slugify

from .const import ACTION_UPDATE_SNAPSHOT, DATA_ISAPI, DOMAIN
from .isapi import ISAPI, CameraStreamInfo

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add images with snapshots."""

    config = hass.data[DOMAIN][entry.entry_id]
    isapi: ISAPI = config[DATA_ISAPI]

    entities = []
    for camera in isapi.cameras:
        for stream in camera.streams:
            if stream.type_id == 1:
                entities.append(SnapshotFile(hass, isapi, camera, stream))

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
        isapi: ISAPI,
        camera: Camera,
        stream_info: CameraStreamInfo,
    ) -> None:
        """Initialize the snapshot file."""

        ImageEntity.__init__(self, hass)

        self._attr_unique_id = slugify(f"{isapi.device_info.serial_no.lower()}_{stream_info.id}_snapshot")
        self.entity_id = f"camera.{self.unique_id}"
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
