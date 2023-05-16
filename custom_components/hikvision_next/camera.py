"""Component providing support for Hikvision IP cameras."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .const import DOMAIN, DATA_ISAPI

from .isapi import StreamInfo


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up a Hikvision IP Camera."""

    config = hass.data[DOMAIN][entry.entry_id]
    isapi = config[DATA_ISAPI]

    entities = []
    for stream in isapi.streams_info:
        entities.append(HikvisionCamera(isapi, stream))

    async_add_entities(entities)


class HikvisionCamera(Camera):
    """An implementation of a Hikvision IP camera."""

    _attr_supported_features: CameraEntityFeature = CameraEntityFeature.STREAM

    def __init__(self, isapi, stream_info: StreamInfo) -> None:
        """Initialize Hikvision camera stream."""
        Camera.__init__(self)

        self._attr_device_info = stream_info.device_info
        self._attr_name = stream_info.name
        self._attr_unique_id = slugify(f"{isapi.serial_no.lower()}_{stream_info.id}")
        self.entity_id = f"camera.{self.unique_id}"
        self.isapi = isapi
        self.stream_info = stream_info

    async def stream_source(self) -> str | None:
        """Return the source of the stream."""
        return self.isapi.get_stream_source(self.stream_info)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image response from the camera."""
        return await self.isapi.get_camera_image(self.stream_info, width, height)
