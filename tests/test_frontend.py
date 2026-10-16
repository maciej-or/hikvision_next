"""Bundled card registration and safe camera control metadata."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.hikvision_next.const import CONTROL_PANEL_URL
from custom_components.hikvision_next.frontend import CARD_PATH, CARD_URL, DATA_REGISTERED, async_register_card


async def test_register_once_and_cache_bust_card():
    hass = SimpleNamespace(
        data={},
        config=SimpleNamespace(components={"frontend"}),
        http=SimpleNamespace(async_register_static_paths=AsyncMock()),
        async_add_executor_job=AsyncMock(side_effect=lambda work: work()),
    )
    with patch("custom_components.hikvision_next.frontend.add_extra_js_url") as add_url:
        await async_register_card(hass)
        await async_register_card(hass)
    configs = hass.http.async_register_static_paths.await_args.args[0]
    assert len(configs) == 1
    assert configs[0].url_path == CARD_URL
    assert configs[0].path == str(CARD_PATH)
    assert not configs[0].cache_headers
    assert CARD_PATH.is_file()
    assert hass.data[DATA_REGISTERED].startswith(f"{CARD_URL}?v=")
    hass.http.async_register_static_paths.assert_awaited_once()
    add_url.assert_called_once_with(hass, hass.data[DATA_REGISTERED])


async def test_headless_install_needs_no_frontend():
    hass = SimpleNamespace(data={}, config=SimpleNamespace(components=set()))
    await async_register_card(hass)
    assert not hass.data


async def test_automatic_module_with_real_frontend(hass, hass_client):
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, DATA_PANELS
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "frontend", {})
    await async_register_card(hass)
    assert hass.data[DATA_REGISTERED] in hass.data[DATA_EXTRA_MODULE_URL].urls
    panel = hass.data[DATA_PANELS][CONTROL_PANEL_URL]
    assert panel.component_name == "lovelace"
    assert panel.sidebar_title is None  # Device entry point, no sidebar clutter.
    assert panel.require_admin is False
    page = hass.data[LOVELACE_DATA].dashboards[CONTROL_PANEL_URL]
    default = hass.data[LOVELACE_DATA].dashboards[None]
    await async_register_card(hass)
    assert hass.data[LOVELACE_DATA].dashboards[None] is default
    assert (await page.async_load(False))["views"][0]["cards"] == [{"type": "custom:hikvision-camera-control"}]
    client = await hass_client()
    response = await client.get(f"/{CONTROL_PANEL_URL}/0?entry=test&channel=34")
    assert response.status == 200
    assert response.content_type == "text/html"


async def test_device_control_page_websocket_and_read_only(hass, hass_ws_client):
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "frontend", {})
    await async_register_card(hass)
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "lovelace/config", "url_path": CONTROL_PANEL_URL})
    response = await client.receive_json()
    assert response["success"]
    assert response["result"]["views"][0]["cards"] == [{"type": "custom:hikvision-camera-control"}]
    await client.send_json(
        {"id": 2, "type": "lovelace/config/save", "url_path": CONTROL_PANEL_URL, "config": {"views": []}}
    )
    response = await client.receive_json()
    assert not response["success"]
    assert response["error"]["message"] == "Not supported"


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_camera_card_control_metadata(hass, init_integration):
    entity = next(state for state in hass.states.async_all("camera"))
    data = entity.attributes["hikvision_next"]
    assert data["config_entry_id"] == init_integration.entry_id
    assert data["channel_id"] == 1
    assert data["stream_id"] == 101
    assert data["stream_type"] == 1
    assert isinstance(data["ptz"], bool)
    assert data["intercom"] is False
    assert set(data) == {"config_entry_id", "channel_id", "stream_id", "stream_type", "ptz", "intercom"}


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_device_registry_links_scope_entry_and_channel(hass, init_integration):
    from urllib.parse import parse_qs, urlsplit

    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    device = init_integration.runtime_data
    registry = dr.async_get(hass)
    parent = registry.async_get_device(identifiers={("hikvision_next", device.device_info.serial_no)})
    parent_url = urlsplit(parent.configuration_url)
    assert parent_url.scheme == "homeassistant"
    assert parent_url.netloc == CONTROL_PANEL_URL
    assert parse_qs(parent_url.query) == {"entry": [init_integration.entry_id]}
    entity = next(state for state in hass.states.async_all("camera"))
    camera_entry = er.async_get(hass).async_get(entity.entity_id)
    child = registry.async_get(camera_entry.device_id)
    child_url = urlsplit(child.configuration_url)
    assert child_url.path == "/0"
    assert parse_qs(child_url.query) == {"entry": [init_integration.entry_id], "channel": ["1"]}


async def test_static_javascript_is_served(hass, hass_client):
    """Exercise the actual aiohttp static route, without a full UI startup."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "http", {})
    # Isolate frontend registration; frontend itself is not needed for HTTP.
    hass.config.components.add("frontend")
    with patch("custom_components.hikvision_next.frontend.add_extra_js_url"):
        await async_register_card(hass)
    client = await hass_client()
    response = await client.get(hass.data[DATA_REGISTERED])
    assert response.status == 200
    assert "javascript" in response.content_type
    assert "hikvision-camera-card" in await response.text()
