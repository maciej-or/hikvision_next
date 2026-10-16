"""Component providing support for Hikvision IP cameras."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from . import HikvisionConfigEntry
from .const import CONF_CONNECTION_SDK, CONF_SDK_WEBRTC
from .hikvision_device import HikvisionDevice
from .isapi import AnalogCamera, CameraStreamInfo, IPCamera


async def async_setup_entry(
    hass: HomeAssistant, entry: HikvisionConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up a Hikvision IP Camera."""

    device = entry.runtime_data
    use_sdk_webrtc = entry.data.get(CONF_SDK_WEBRTC, False) and device.connection_type == CONF_CONNECTION_SDK

    entities = []
    for camera in device.cameras:
        for stream in camera.streams:
            entity_class = HikvisionCamera
            if use_sdk_webrtc and stream.codec.upper().replace(".", "") == "H264" and 1 <= stream.type_id <= 11:
                entity_class = HikvisionWebRTCCamera
            entities.append(entity_class(device, camera, stream))

    async_add_entities(entities)


class HikvisionCamera(Camera):
    """An implementation of a Hikvision IP camera."""

    _attr_supported_features: CameraEntityFeature = CameraEntityFeature.STREAM

    def __init__(
        self,
        device: HikvisionDevice,
        camera: AnalogCamera | IPCamera,
        stream_info: CameraStreamInfo,
    ) -> None:
        """Initialize Hikvision camera stream."""
        Camera.__init__(self)

        self._attr_device_info = device.hass_device_info(camera.id)
        self._attr_unique_id = slugify(f"{device.device_info.serial_no.lower()}_{stream_info.id}")
        if stream_info.type_id > 1:
            self._attr_has_entity_name = True
            self._attr_translation_key = f"stream{stream_info.type_id}"
            self._attr_entity_registry_enabled_default = False
        else:
            # for the main stream use just its name
            self._attr_name = camera.name
        self.entity_id = f"camera.{self.unique_id}"
        self.device = device
        self.stream_info = stream_info
        # Safe control/discovery metadata for the bundled card. Do not expose
        # device URLs, usernames, passwords or SDK handles to the frontend.
        self._attr_extra_state_attributes = {
            "hikvision_next": {
                "config_entry_id": device.entry.entry_id,
                "channel_id": camera.id,
                "stream_id": stream_info.id,
                "stream_type": stream_info.type_id,
                "ptz": camera.support_ptz,
                "intercom": (
                    device.connection_type == CONF_CONNECTION_SDK
                    and device.capabilities.support_video_intercom
                ),
            }
        }

    async def stream_source(self) -> str | None:
        """Return the source of the stream."""
        return self.device.get_stream_source(self.stream_info)

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        """Return a still image response from the camera."""
        return await self.device.get_camera_image(self.stream_info, width, height)


class HikvisionWebRTCCamera(HikvisionCamera):
    """SDK H.264 camera using HA's authenticated WebRTC signaling."""

    def _manager(self):
        # Import only for opted-in cameras, avoiding any WebRTC initialization
        # or native dependency imports for existing RTSP installations.
        from .webrtc import SDKWebRTCManager

        if self.device.webrtc_manager is None:
            self.device.webrtc_manager = SDKWebRTCManager(self.device)
        return self.device.webrtc_manager

    async def async_handle_async_webrtc_offer(self, offer_sdp, session_id, send_message):
        await self._manager().offer(self.stream_info, offer_sdp, session_id, send_message)

    async def async_on_webrtc_candidate(self, session_id, candidate):
        if self.device.webrtc_manager:
            await self.device.webrtc_manager.candidate(session_id, candidate)

    @callback
    def close_webrtc_session(self, session_id):
        if self.device.webrtc_manager:
            self.device.webrtc_manager.close_session(session_id)

    async def async_will_remove_from_hass(self):
        if manager := self.device.webrtc_manager:
            for session_id, session in list(manager.sessions.items()):
                if session.preview and session.preview.stream.id == self.stream_info.id:
                    manager.close_session(session_id)
        await super().async_will_remove_from_hass()
