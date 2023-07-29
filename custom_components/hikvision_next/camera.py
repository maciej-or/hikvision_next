"""Component providing support for Hikvision IP cameras."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .const import DATA_ISAPI, DOMAIN
from .isapi import ISAPI, AnalogCamera, CameraStreamInfo, IPCamera


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up a Hikvision IP Camera."""

    config = hass.data[DOMAIN][entry.entry_id]
    isapi: ISAPI = config[DATA_ISAPI]

    entities = []
    for camera in isapi.cameras:
        for stream in camera.streams:
            entities.append(HikvisionCamera(isapi, camera, stream))

    async_add_entities(entities)


class HikvisionCamera(Camera):
    """An implementation of a Hikvision IP camera."""

    _attr_supported_features: CameraEntityFeature = CameraEntityFeature.STREAM

    def __init__(
        self,
        isapi: ISAPI,
        camera: AnalogCamera | IPCamera,
        stream_info: CameraStreamInfo,
    ) -> None:
        """Initialize Hikvision camera stream."""
        Camera.__init__(self)

        self._attr_device_info = isapi.hass_device_info(camera.id)
        self._attr_name = f"{camera.name} {stream_info.type}"
        self._attr_unique_id = slugify(f"{isapi.device_info.serial_no.lower()}_{stream_info.id}")
        self.entity_id = f"camera.{self.unique_id}"
        self.isapi = isapi
        self.stream_info = stream_info

    async def stream_source(self) -> str | None:
        """Return the source of the stream."""
        return self.isapi.get_stream_source(self.stream_info)

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        """Return a still image response from the camera."""
        data = await self.isapi.get_camera_image(self.stream_info, width, height)
        if data.startswith(b'<?xml '):
            # retry if got XML error response
            data = await self.isapi.get_camera_image(self.stream_info, width, height)
        return data
