"""Tests for overlay_manager module."""

import pytest
from datetime import datetime
from custom_components.hikvision_next.overlay_manager import (
    _build_overlay_xml,
    _format_text,
)


def test_build_overlay_xml_basic():
    """Test building overlay XML with basic parameters."""
    xml = _build_overlay_xml(
        overlay_id=1,
        enabled=True,
        position_x=100,
        position_y=50,
        display_text="Test Camera",
    )

    assert "<id>1</id>" in xml
    assert "<enabled>true</enabled>" in xml
    assert "<positionX>100</positionX>" in xml
    assert "<positionY>50</positionY>" in xml
    assert "<displayText>Test Camera</displayText>" in xml
    assert 'xmlns="http://www.hikvision.com/ver20/XMLSchema"' in xml


def test_build_overlay_xml_disabled():
    """Test building overlay XML with disabled state."""
    xml = _build_overlay_xml(
        overlay_id=1,
        enabled=False,
        position_x=16,
        position_y=570,
        display_text="Disabled",
    )

    assert "<enabled>false</enabled>" in xml
    assert "<displayText>Disabled</displayText>" in xml


def test_build_overlay_xml_special_characters():
    """Test building overlay XML with special characters in text."""
    xml = _build_overlay_xml(
        overlay_id=1,
        enabled=True,
        position_x=0,
        position_y=0,
        display_text="Front & Rear",
    )

    assert "<displayText>Front & Rear</displayText>" in xml


def test_format_text_fixed_mode():
    """Test formatting text in fixed mode."""
    config = {
        "mode": "fixed",
        "text": "Front Door Camera",
    }

    result = _format_text(config)
    assert result == "Front Door Camera"


def test_format_text_datetime_mode():
    """Test formatting text in datetime mode."""
    config = {
        "mode": "datetime",
        "text": "%Y-%m-%d %H:%M:%S",
    }

    result = _format_text(config)
    # Verify it's a valid datetime string
    datetime.strptime(result, "%Y-%m-%d %H:%M:%S")


def test_format_text_datetime_custom_format():
    """Test formatting text with custom datetime format."""
    config = {
        "mode": "datetime",
        "text": "%B %d, %I:%M %p",
    }

    result = _format_text(config)
    # Verify format matches pattern (e.g., "October 21, 02:30 PM")
    assert "," in result
    assert len(result) > 10


def test_format_text_default_mode():
    """Test formatting text without explicit mode (defaults to fixed)."""
    config = {
        "text": "Default Mode",
    }

    result = _format_text(config)
    assert result == "Default Mode"
