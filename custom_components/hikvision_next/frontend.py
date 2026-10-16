"""Serve the bundled camera card and its device control entry page."""

import hashlib
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_YAML
from homeassistant.components.lovelace.dashboard import LovelaceConfig
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.json import json_bytes, json_fragment

from .const import CONTROL_PANEL_URL

CARD_PATH = Path(__file__).parent / "www" / "hikvision-camera-card.js"
CARD_URL = "/hikvision_next/hikvision-camera-card.js"
DATA_REGISTERED = "hikvision_next_frontend"


class CameraControlPage(LovelaceConfig):
    """In-memory integration-owned view; no user dashboard is modified.

    HA's card runtime also loads native media components when this page is
    opened directly from a device, before any user dashboard was visited.
    """

    @property
    def mode(self) -> str:
        return MODE_YAML

    async def async_get_info(self) -> dict:
        return {"mode": self.mode, "views": 1}

    async def async_load(self, force: bool) -> dict:
        return {
            "title": "Hikvision Camera",
            "views": [
                {
                    "path": "0",
                    "title": "Hikvision Camera",
                    "type": "panel",
                    "cards": [{"type": "custom:hikvision-camera-control"}],
                }
            ],
        }

    async def async_json(self, force: bool) -> json_fragment:
        return json_fragment(json_bytes(await self.async_load(force)))


def _register_control_page(hass: HomeAssistant) -> None:
    # Frontend depends on lovelace. Minimal HTTP-only installations have no UI.
    if LOVELACE_DATA not in hass.data:
        return
    dashboards = hass.data[LOVELACE_DATA].dashboards
    if CONTROL_PANEL_URL in dashboards:
        if isinstance(dashboards[CONTROL_PANEL_URL], CameraControlPage):
            return
        raise HomeAssistantError(f"Control page URL {CONTROL_PANEL_URL} is already in use")
    frontend.async_register_built_in_panel(
        hass,
        component_name="lovelace",
        frontend_url_path=CONTROL_PANEL_URL,
        config={"mode": MODE_YAML},
    )
    dashboards[CONTROL_PANEL_URL] = CameraControlPage(
        hass, CONTROL_PANEL_URL, {"title": "Hikvision Camera", "require_admin": False}
    )


async def async_register_card(hass: HomeAssistant) -> None:
    """Register once for all config entries; support installations without a UI."""
    if DATA_REGISTERED in hass.data or "frontend" not in hass.config.components:
        return
    digest = await hass.async_add_executor_job(lambda: hashlib.sha256(CARD_PATH.read_bytes()).hexdigest()[:12])
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_PATH), False)])
    url = f"{CARD_URL}?v={digest}"
    add_extra_js_url(hass, url)
    _register_control_page(hass)
    hass.data[DATA_REGISTERED] = url
