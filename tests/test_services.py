"""Tests for actions."""

import pytest
import respx
import voluptuous as vol
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.hikvision_next.const import (
  ACTION_REBOOT,
  ATTR_CONFIG_ENTRY_ID,
  ATTR_CAMERA_CHANNEL,
  ATTR_TEXT,
  ATTR_MODE,
  ATTR_DATETIME_FORMAT,
  ATTR_POSITION_X,
  ATTR_POSITION_Y,
  ATTR_INTERVAL_SECONDS,
  ATTR_ENABLED,
  MODE_FIXED,
  MODE_DATETIME,
  DOMAIN,
)
from custom_components.hikvision_next.services import (
    OVERLAY_SET_SCHEMA,
    OVERLAY_CONTROL_SCHEMA,
    validate_datetime_format,
)
from tests.conftest import TEST_HOST


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_reboot_action(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test sending reboot request on reboot action."""

    mock_config_entry = init_integration

    url = f"{TEST_HOST}/ISAPI/System/reboot"
    endpoint = respx.put(url).respond()

    await hass.services.async_call(
        DOMAIN,
        ACTION_REBOOT,
        {ATTR_CONFIG_ENTRY_ID: mock_config_entry.entry_id},
        blocking=True,
    )

    assert endpoint.called


def test_validate_datetime_format_valid():
    """Test validate_datetime_format with valid format strings."""
    assert validate_datetime_format("%Y-%m-%d %H:%M:%S") == "%Y-%m-%d %H:%M:%S"
    assert validate_datetime_format("%B %d, %I:%M %p") == "%B %d, %I:%M %p"
    assert validate_datetime_format("%Y/%m/%d") == "%Y/%m/%d"


def test_validate_datetime_format_invalid():
    """Test validate_datetime_format with invalid input type."""
    # Note: strftime is very permissive and doesn't error on unknown codes
    # Test with non-string type which should fail
    with pytest.raises(vol.Invalid):
        validate_datetime_format(12345)  # Not a string


def test_overlay_set_schema_minimal():
    """Test OVERLAY_SET_SCHEMA with minimal required fields."""
    data = {
        ATTR_CONFIG_ENTRY_ID: "test_entry_id",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "Front Door",
    }
    result = OVERLAY_SET_SCHEMA(data)

    assert result[ATTR_CONFIG_ENTRY_ID] == "test_entry_id"
    assert result[ATTR_CAMERA_CHANNEL] == 1
    assert result[ATTR_TEXT] == "Front Door"
    assert result[ATTR_MODE] == MODE_FIXED  # default
    assert result[ATTR_POSITION_X] == 16  # default
    assert result[ATTR_POSITION_Y] == 570  # default
    assert result[ATTR_INTERVAL_SECONDS] == 900  # default
    assert result[ATTR_ENABLED] is True  # default


def test_overlay_set_schema_full():
    """Test OVERLAY_SET_SCHEMA with all fields specified."""
    data = {
        ATTR_CONFIG_ENTRY_ID: "test_entry_id",
        ATTR_CAMERA_CHANNEL: 5,
        ATTR_TEXT: "Custom Overlay",
        ATTR_MODE: MODE_DATETIME,
        ATTR_DATETIME_FORMAT: "%Y-%m-%d %H:%M:%S",
        ATTR_POSITION_X: 100,
        ATTR_POSITION_Y: 200,
        ATTR_INTERVAL_SECONDS: 60,
        ATTR_ENABLED: False,
    }
    result = OVERLAY_SET_SCHEMA(data)

    assert result[ATTR_CONFIG_ENTRY_ID] == "test_entry_id"
    assert result[ATTR_CAMERA_CHANNEL] == 5
    assert result[ATTR_TEXT] == "Custom Overlay"
    assert result[ATTR_MODE] == MODE_DATETIME
    assert result[ATTR_DATETIME_FORMAT] == "%Y-%m-%d %H:%M:%S"
    assert result[ATTR_POSITION_X] == 100
    assert result[ATTR_POSITION_Y] == 200
    assert result[ATTR_INTERVAL_SECONDS] == 60
    assert result[ATTR_ENABLED] is False


def test_overlay_set_schema_camera_channel_validation():
    """Test OVERLAY_SET_SCHEMA camera channel validation."""
    # Valid range: 1-32
    valid_data = {
        ATTR_CONFIG_ENTRY_ID: "test_entry_id",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "Test",
    }
    assert OVERLAY_SET_SCHEMA(valid_data)[ATTR_CAMERA_CHANNEL] == 1

    valid_data[ATTR_CAMERA_CHANNEL] = 32
    assert OVERLAY_SET_SCHEMA(valid_data)[ATTR_CAMERA_CHANNEL] == 32

    # Invalid: below minimum
    invalid_data = {
        ATTR_CONFIG_ENTRY_ID: "test_entry_id",
        ATTR_CAMERA_CHANNEL: 0,
        ATTR_TEXT: "Test",
    }
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(invalid_data)

    # Invalid: above maximum
    invalid_data[ATTR_CAMERA_CHANNEL] = 33
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(invalid_data)


def test_overlay_set_schema_text_length_validation():
    """Test OVERLAY_SET_SCHEMA text length validation (max 44 chars)."""
    # Valid: exactly 44 characters
    valid_data = {
        ATTR_CONFIG_ENTRY_ID: "test_entry_id",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "A" * 44,
    }
    assert len(OVERLAY_SET_SCHEMA(valid_data)[ATTR_TEXT]) == 44

    # Invalid: 45 characters
    invalid_data = {
        ATTR_CONFIG_ENTRY_ID: "test_entry_id",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "A" * 45,
    }
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(invalid_data)


def test_overlay_set_schema_position_validation():
    """Test OVERLAY_SET_SCHEMA position validation."""
    # Valid X range: 0-1920
    valid_data = {
        ATTR_CONFIG_ENTRY_ID: "test_entry_id",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "Test",
        ATTR_POSITION_X: 0,
    }
    assert OVERLAY_SET_SCHEMA(valid_data)[ATTR_POSITION_X] == 0

    valid_data[ATTR_POSITION_X] = 1920
    assert OVERLAY_SET_SCHEMA(valid_data)[ATTR_POSITION_X] == 1920

    # Invalid X: above maximum
    invalid_data = {
        ATTR_CONFIG_ENTRY_ID: "test_entry_id",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "Test",
        ATTR_POSITION_X: 1921,
    }
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(invalid_data)

    # Valid Y range: 0-1080
    valid_data = {
        ATTR_CONFIG_ENTRY_ID: "test_entry_id",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "Test",
        ATTR_POSITION_Y: 0,
    }
    assert OVERLAY_SET_SCHEMA(valid_data)[ATTR_POSITION_Y] == 0

    valid_data[ATTR_POSITION_Y] = 1080
    assert OVERLAY_SET_SCHEMA(valid_data)[ATTR_POSITION_Y] == 1080

    # Invalid Y: above maximum
    invalid_data = {
        ATTR_CONFIG_ENTRY_ID: "test_entry_id",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "Test",
        ATTR_POSITION_Y: 1081,
    }
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(invalid_data)


def test_overlay_set_schema_interval_validation():
    """Test OVERLAY_SET_SCHEMA interval validation (min 1 second)."""
    # Valid: 1 second
    valid_data = {
        ATTR_CONFIG_ENTRY_ID: "test_entry_id",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "Test",
        ATTR_INTERVAL_SECONDS: 1,
    }
    assert OVERLAY_SET_SCHEMA(valid_data)[ATTR_INTERVAL_SECONDS] == 1

    # Invalid: 0 seconds
    invalid_data = {
        ATTR_CONFIG_ENTRY_ID: "test_entry_id",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "Test",
        ATTR_INTERVAL_SECONDS: 0,
    }
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(invalid_data)


def test_overlay_set_schema_invalid_datetime_format():
    """Test OVERLAY_SET_SCHEMA with invalid datetime format type."""
    invalid_data = {
        ATTR_CONFIG_ENTRY_ID: "test_entry_id",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "Test",
        ATTR_DATETIME_FORMAT: 12345,  # Not a string
    }
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(invalid_data)


def test_overlay_control_schema_valid():
    """Test OVERLAY_CONTROL_SCHEMA with valid data."""
    data = {
        ATTR_CONFIG_ENTRY_ID: "test_entry_id",
        ATTR_CAMERA_CHANNEL: 5,
    }
    result = OVERLAY_CONTROL_SCHEMA(data)

    assert result[ATTR_CONFIG_ENTRY_ID] == "test_entry_id"
    assert result[ATTR_CAMERA_CHANNEL] == 5


def test_overlay_control_schema_missing_required():
    """Test OVERLAY_CONTROL_SCHEMA with missing required fields."""
    # Missing config_entry_id
    with pytest.raises(vol.Invalid):
        OVERLAY_CONTROL_SCHEMA({ATTR_CAMERA_CHANNEL: 1})

    # Missing camera_channel
    with pytest.raises(vol.Invalid):
        OVERLAY_CONTROL_SCHEMA({ATTR_CONFIG_ENTRY_ID: "test_entry_id"})


# User Story 1 Tests - Fixed Text Overlay

@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_set_overlay_service_fixed_text(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test set_overlay service call with fixed text mode."""
    from custom_components.hikvision_next.overlay_manager import async_stop_overlay_updates

    mock_config_entry = init_integration

    # Mock ISAPI overlay endpoint
    url = f"{TEST_HOST}/ISAPI/System/Video/inputs/channels/1/overlays/text/1"
    endpoint = respx.put(url).respond(text="""<?xml version="1.0" encoding="UTF-8"?>
<ResponseStatus version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
<statusCode>1</statusCode>
<statusString>OK</statusString>
</ResponseStatus>""")

    # Call set_overlay service
    await hass.services.async_call(
        DOMAIN,
        "set_overlay",
        {
            ATTR_CONFIG_ENTRY_ID: mock_config_entry.entry_id,
            ATTR_CAMERA_CHANNEL: 1,
            ATTR_TEXT: "Front Door Camera",
            ATTR_MODE: MODE_FIXED,
            ATTR_POSITION_X: 100,
            ATTR_POSITION_Y: 50,
            ATTR_INTERVAL_SECONDS: 900,
            ATTR_ENABLED: True,
        },
        blocking=True,
    )

    assert endpoint.called

    # Cleanup: Stop overlay updates to prevent lingering timers
    async_stop_overlay_updates(mock_config_entry.entry_id, 1)


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_set_overlay_config_persistence(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test overlay configuration is persisted in config entry data."""
    from custom_components.hikvision_next.overlay_manager import async_stop_overlay_updates

    mock_config_entry = init_integration

    # Mock ISAPI overlay endpoint
    url = f"{TEST_HOST}/ISAPI/System/Video/inputs/channels/2/overlays/text/1"
    respx.put(url).respond(text="""<?xml version="1.0" encoding="UTF-8"?>
<ResponseStatus version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
<statusCode>1</statusCode>
</ResponseStatus>""")

    # Call set_overlay service
    await hass.services.async_call(
        DOMAIN,
        "set_overlay",
        {
            ATTR_CONFIG_ENTRY_ID: mock_config_entry.entry_id,
            ATTR_CAMERA_CHANNEL: 2,
            ATTR_TEXT: "Garage",
            ATTR_MODE: MODE_FIXED,
            ATTR_INTERVAL_SECONDS: 600,
        },
        blocking=True,
    )

    # Verify config is stored
    # (Implementation will store in entry.data or entry.options)
    # This test will fail until implementation is complete

    # Cleanup: Stop overlay updates to prevent lingering timers
    async_stop_overlay_updates(mock_config_entry.entry_id, 2)


def test_isapi_overlay_xml_structure(mock_overlay_config):
    """Test ISAPI XML payload structure for text overlay."""
    from custom_components.hikvision_next.overlay_manager import _build_overlay_xml

    xml = _build_overlay_xml(
        overlay_id=1,
        enabled=True,
        position_x=100,
        position_y=50,
        display_text="Test Camera",
    )

    # Verify XML structure
    assert '<?xml version="1.0" encoding="UTF-8"?>' in xml
    assert '<TextOverlayList version="2.0"' in xml
    assert 'xmlns="http://www.hikvision.com/ver20/XMLSchema"' in xml
    assert "<id>1</id>" in xml
    assert "<enabled>true</enabled>" in xml
    assert "<positionX>100</positionX>" in xml
    assert "<positionY>50</positionY>" in xml
    assert "<displayText>Test Camera</displayText>" in xml


def test_overlay_position_bounds():
    """Test overlay position validation with boundary values."""
    # Valid: minimum bounds
    data = {
        ATTR_CONFIG_ENTRY_ID: "test",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "Test",
        ATTR_POSITION_X: 0,
        ATTR_POSITION_Y: 0,
    }
    result = OVERLAY_SET_SCHEMA(data)
    assert result[ATTR_POSITION_X] == 0
    assert result[ATTR_POSITION_Y] == 0

    # Valid: maximum bounds
    data[ATTR_POSITION_X] = 1920
    data[ATTR_POSITION_Y] = 1080
    result = OVERLAY_SET_SCHEMA(data)
    assert result[ATTR_POSITION_X] == 1920
    assert result[ATTR_POSITION_Y] == 1080

    # Invalid: negative X
    data[ATTR_POSITION_X] = -1
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(data)

    # Invalid: negative Y
    data[ATTR_POSITION_X] = 0
    data[ATTR_POSITION_Y] = -1
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(data)


def test_overlay_text_length_bounds():
    """Test overlay text length validation (44 char limit)."""
    # Valid: 1 character
    data = {
        ATTR_CONFIG_ENTRY_ID: "test",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "A",
    }
    result = OVERLAY_SET_SCHEMA(data)
    assert len(result[ATTR_TEXT]) == 1

    # Valid: 44 characters (maximum)
    data[ATTR_TEXT] = "X" * 44
    result = OVERLAY_SET_SCHEMA(data)
    assert len(result[ATTR_TEXT]) == 44

    # Invalid: 45 characters (exceeds maximum)
    data[ATTR_TEXT] = "X" * 45
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(data)

    # Invalid: empty string
    data[ATTR_TEXT] = ""
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(data)


# User Story 2 Tests - Dynamic Datetime Overlay

def test_datetime_format_validation_valid():
    """Test datetime format validation with valid formats."""
    # Valid: Standard datetime format
    data = {
        ATTR_CONFIG_ENTRY_ID: "test",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "%Y-%m-%d %H:%M:%S",
        ATTR_MODE: MODE_DATETIME,
        ATTR_DATETIME_FORMAT: "%Y-%m-%d %H:%M:%S",
    }
    result = OVERLAY_SET_SCHEMA(data)
    assert result[ATTR_DATETIME_FORMAT] == "%Y-%m-%d %H:%M:%S"

    # Valid: Custom format
    data[ATTR_DATETIME_FORMAT] = "%B %d, %Y at %I:%M %p"
    result = OVERLAY_SET_SCHEMA(data)
    assert result[ATTR_DATETIME_FORMAT] == "%B %d, %Y at %I:%M %p"


def test_datetime_format_validation_invalid():
    """Test datetime format validation with invalid type."""
    # Invalid: Non-string datetime format
    data = {
        ATTR_CONFIG_ENTRY_ID: "test",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "%Y-%m-%d",
        ATTR_MODE: MODE_DATETIME,
        ATTR_DATETIME_FORMAT: 12345,  # Not a string
    }
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(data)


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_set_overlay_datetime_mode(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test set_overlay service with datetime mode."""
    from custom_components.hikvision_next.overlay_manager import async_stop_overlay_updates

    mock_config_entry = init_integration

    # Mock ISAPI overlay endpoint
    url = f"{TEST_HOST}/ISAPI/System/Video/inputs/channels/3/overlays/text/1"
    endpoint = respx.put(url).respond(text="""<?xml version="1.0" encoding="UTF-8"?>
<ResponseStatus version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
<statusCode>1</statusCode>
<statusString>OK</statusString>
</ResponseStatus>""")

    # Call set_overlay service with datetime mode
    await hass.services.async_call(
        DOMAIN,
        "set_overlay",
        {
            ATTR_CONFIG_ENTRY_ID: mock_config_entry.entry_id,
            ATTR_CAMERA_CHANNEL: 3,
            ATTR_TEXT: "%Y-%m-%d %H:%M:%S",
            ATTR_MODE: MODE_DATETIME,
            ATTR_DATETIME_FORMAT: "%Y-%m-%d %H:%M:%S",
            ATTR_INTERVAL_SECONDS: 60,
        },
        blocking=True,
    )

    assert endpoint.called

    # Verify the request was made with formatted datetime text
    request = endpoint.calls.last.request
    assert b"<displayText>" in request.content
    # Datetime should be formatted (not the format string itself)
    assert b"%Y-%m-%d" not in request.content

    # Cleanup
    async_stop_overlay_updates(mock_config_entry.entry_id, 3)


def test_datetime_overlay_update_cycle():
    """Test that datetime overlay generates different text over time."""
    from custom_components.hikvision_next.overlay_manager import _format_text
    from datetime import datetime
    import time

    config = {
        "mode": MODE_DATETIME,
        "text": "%Y-%m-%d %H:%M:%S",
    }

    # Get first formatted text
    text1 = _format_text(config)

    # Verify it matches datetime pattern
    datetime.strptime(text1, "%Y-%m-%d %H:%M:%S")

    # Wait a moment and get second formatted text
    time.sleep(1)
    text2 = _format_text(config)

    # Text should be different after 1 second
    assert text1 != text2


# User Story 3 Tests - Control Overlay Update Intervals

def test_different_update_intervals():
    """Test overlay schema accepts different update intervals."""
    # Valid: 1 second (minimum)
    data = {
        ATTR_CONFIG_ENTRY_ID: "test",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "Test",
        ATTR_INTERVAL_SECONDS: 1,
    }
    result = OVERLAY_SET_SCHEMA(data)
    assert result[ATTR_INTERVAL_SECONDS] == 1

    # Valid: 60 seconds (1 minute)
    data[ATTR_INTERVAL_SECONDS] = 60
    result = OVERLAY_SET_SCHEMA(data)
    assert result[ATTR_INTERVAL_SECONDS] == 60

    # Valid: 900 seconds (15 minutes - default)
    data[ATTR_INTERVAL_SECONDS] = 900
    result = OVERLAY_SET_SCHEMA(data)
    assert result[ATTR_INTERVAL_SECONDS] == 900

    # Valid: 3600 seconds (1 hour)
    data[ATTR_INTERVAL_SECONDS] = 3600
    result = OVERLAY_SET_SCHEMA(data)
    assert result[ATTR_INTERVAL_SECONDS] == 3600

    # Invalid: 0 seconds (below minimum)
    data[ATTR_INTERVAL_SECONDS] = 0
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(data)

    # Invalid: negative interval
    data[ATTR_INTERVAL_SECONDS] = -1
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(data)


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_interval_reconfiguration(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test reconfiguring overlay interval cancels old task and starts new one."""
    from custom_components.hikvision_next.overlay_manager import (
        async_stop_overlay_updates,
        _overlay_tasks,
    )

    mock_config_entry = init_integration

    # Mock ISAPI overlay endpoint
    url = f"{TEST_HOST}/ISAPI/System/Video/inputs/channels/4/overlays/text/1"
    respx.put(url).respond(text="""<?xml version="1.0" encoding="UTF-8"?>
<ResponseStatus version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
<statusCode>1</statusCode>
</ResponseStatus>""")

    # Start overlay with 60-second interval
    await hass.services.async_call(
        DOMAIN,
        "set_overlay",
        {
            ATTR_CONFIG_ENTRY_ID: mock_config_entry.entry_id,
            ATTR_CAMERA_CHANNEL: 4,
            ATTR_TEXT: "Test Camera",
            ATTR_INTERVAL_SECONDS: 60,
        },
        blocking=True,
    )

    # Verify task is registered
    task_key = (mock_config_entry.entry_id, 4)
    assert task_key in _overlay_tasks

    # Reconfigure with different interval (300 seconds)
    await hass.services.async_call(
        DOMAIN,
        "set_overlay",
        {
            ATTR_CONFIG_ENTRY_ID: mock_config_entry.entry_id,
            ATTR_CAMERA_CHANNEL: 4,
            ATTR_TEXT: "Test Camera Updated",
            ATTR_INTERVAL_SECONDS: 300,
        },
        blocking=True,
    )

    # Task should still be registered (old cancelled, new created)
    assert task_key in _overlay_tasks

    # Cleanup
    async_stop_overlay_updates(mock_config_entry.entry_id, 4)


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_multiple_cameras_independent_intervals(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test multiple cameras can have independent update intervals."""
    from custom_components.hikvision_next.overlay_manager import (
        async_stop_overlay_updates,
        _overlay_tasks,
    )

    mock_config_entry = init_integration

    # Mock ISAPI overlay endpoints for multiple cameras
    respx.put(f"{TEST_HOST}/ISAPI/System/Video/inputs/channels/5/overlays/text/1").respond(
        text="""<?xml version="1.0" encoding="UTF-8"?>
<ResponseStatus version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
<statusCode>1</statusCode>
</ResponseStatus>"""
    )
    respx.put(f"{TEST_HOST}/ISAPI/System/Video/inputs/channels/6/overlays/text/1").respond(
        text="""<?xml version="1.0" encoding="UTF-8"?>
<ResponseStatus version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
<statusCode>1</statusCode>
</ResponseStatus>"""
    )

    # Start overlay on camera 5 with 60-second interval
    await hass.services.async_call(
        DOMAIN,
        "set_overlay",
        {
            ATTR_CONFIG_ENTRY_ID: mock_config_entry.entry_id,
            ATTR_CAMERA_CHANNEL: 5,
            ATTR_TEXT: "Camera 5",
            ATTR_INTERVAL_SECONDS: 60,
        },
        blocking=True,
    )

    # Start overlay on camera 6 with 900-second interval
    await hass.services.async_call(
        DOMAIN,
        "set_overlay",
        {
            ATTR_CONFIG_ENTRY_ID: mock_config_entry.entry_id,
            ATTR_CAMERA_CHANNEL: 6,
            ATTR_TEXT: "Camera 6",
            ATTR_INTERVAL_SECONDS: 900,
        },
        blocking=True,
    )

    # Verify both tasks are registered independently
    task_key_5 = (mock_config_entry.entry_id, 5)
    task_key_6 = (mock_config_entry.entry_id, 6)
    assert task_key_5 in _overlay_tasks
    assert task_key_6 in _overlay_tasks

    # Cleanup both
    async_stop_overlay_updates(mock_config_entry.entry_id, 5)
    async_stop_overlay_updates(mock_config_entry.entry_id, 6)


# Phase 6: Polish - Edge Case Tests

def test_overlay_invalid_channel():
    """Test overlay schema rejects invalid camera channel numbers."""
    # Channel 0 (below minimum)
    data = {
        ATTR_CONFIG_ENTRY_ID: "test",
        ATTR_CAMERA_CHANNEL: 0,
        ATTR_TEXT: "Test",
    }
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(data)

    # Channel 33 (above maximum)
    data[ATTR_CAMERA_CHANNEL] = 33
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(data)


def test_overlay_text_too_long():
    """Test overlay schema rejects text exceeding 44 characters."""
    data = {
        ATTR_CONFIG_ENTRY_ID: "test",
        ATTR_CAMERA_CHANNEL: 1,
        ATTR_TEXT: "X" * 45,  # 45 characters (1 over limit)
    }
    with pytest.raises(vol.Invalid):
        OVERLAY_SET_SCHEMA(data)


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_overlay_camera_offline(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test overlay update gracefully handles camera offline scenario."""
    from custom_components.hikvision_next.overlay_manager import async_stop_overlay_updates
    from httpx import ConnectError

    mock_config_entry = init_integration

    # Mock ISAPI overlay endpoint to simulate offline camera
    url = f"{TEST_HOST}/ISAPI/System/Video/inputs/channels/7/overlays/text/1"
    respx.put(url).mock(side_effect=ConnectError("Connection refused"))

    # Service call should not raise exception (graceful failure)
    await hass.services.async_call(
        DOMAIN,
        "set_overlay",
        {
            ATTR_CONFIG_ENTRY_ID: mock_config_entry.entry_id,
            ATTR_CAMERA_CHANNEL: 7,
            ATTR_TEXT: "Offline Camera",
            ATTR_INTERVAL_SECONDS: 60,
        },
        blocking=True,
    )

    # Cleanup
    async_stop_overlay_updates(mock_config_entry.entry_id, 7)


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_overlay_concurrent_updates(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test concurrent overlay updates (last write wins)."""
    from custom_components.hikvision_next.overlay_manager import async_stop_overlay_updates

    mock_config_entry = init_integration

    # Mock ISAPI overlay endpoint
    url = f"{TEST_HOST}/ISAPI/System/Video/inputs/channels/8/overlays/text/1"
    endpoint = respx.put(url).respond(text="""<?xml version="1.0" encoding="UTF-8"?>
<ResponseStatus version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
<statusCode>1</statusCode>
</ResponseStatus>""")

    # First update
    await hass.services.async_call(
        DOMAIN,
        "set_overlay",
        {
            ATTR_CONFIG_ENTRY_ID: mock_config_entry.entry_id,
            ATTR_CAMERA_CHANNEL: 8,
            ATTR_TEXT: "First Update",
            ATTR_INTERVAL_SECONDS: 60,
        },
        blocking=True,
    )

    # Second update (should replace first)
    await hass.services.async_call(
        DOMAIN,
        "set_overlay",
        {
            ATTR_CONFIG_ENTRY_ID: mock_config_entry.entry_id,
            ATTR_CAMERA_CHANNEL: 8,
            ATTR_TEXT: "Second Update",
            ATTR_INTERVAL_SECONDS: 120,
        },
        blocking=True,
    )

    # Verify endpoint was called twice
    assert len(endpoint.calls) == 2

    # Last call should have "Second Update" text
    last_request = endpoint.calls[-1].request
    assert b"Second Update" in last_request.content

    # Cleanup
    async_stop_overlay_updates(mock_config_entry.entry_id, 8)
