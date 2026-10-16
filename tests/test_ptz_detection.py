"""Unit tests for PTZ capability detection."""

import pytest
import respx

from custom_components.hikvision_next.isapi.isapi import ISAPIClient
from custom_components.hikvision_next.isapi.utils import (
    input_proxy_cap_indicates_ptz,
    ptz_channel_cap_indicates_support,
)
from tests.conftest import TEST_HOST, mock_device_endpoints


def test_channel_description_is_ptz():
    assert ISAPIClient._channel_description_is_ptz("PTZ")
    assert ISAPIClient._channel_description_is_ptz("ptz")
    assert not ISAPIClient._channel_description_is_ptz("Panoramic")
    assert not ISAPIClient._channel_description_is_ptz(None)


def test_ptz_channel_cap_direct_minimal_is_supported():
    cap = {"@xmlns": "http://www.isapi.org/ver20/XMLSchema", "@version": "2.0", "id": "1"}
    assert ptz_channel_cap_indicates_support(cap, proxied=False)


def test_ptz_channel_cap_proxied_minimal_is_not_supported():
    cap = {"@xmlns": "http://www.isapi.org/ver20/XMLSchema", "@version": "2.0", "id": "34"}
    assert not ptz_channel_cap_indicates_support(cap, proxied=True)


def test_ptz_channel_cap_proxied_with_continuous_is_supported():
    cap = {
        "id": "34",
        "ContinuousPanTiltZoom": {"isSupportContinuous": "true"},
    }
    assert ptz_channel_cap_indicates_support(cap, proxied=True)


def test_input_proxy_cap_indicates_ptz():
    assert input_proxy_cap_indicates_ptz(
        {"InputProxyChannelCap": {"isSupportPTZ": "true"}}
    )
    assert not input_proxy_cap_indicates_ptz(
        {"InputProxyChannelCap": {"id": "2"}}
    )


@respx.mock
async def test_nvr_proxied_channels_only_mark_real_ptz() -> None:
    """NVR proxy PTZ capabilities must not mark every channel as PTZ."""
    mock_device_endpoints("DS-7732NI-M4")
    ptz_url = f"{TEST_HOST}/ISAPI/ContentMgmt/PTZCtrlProxy/channels/1/capabilities"
    fixed_url = f"{TEST_HOST}/ISAPI/ContentMgmt/PTZCtrlProxy/channels/2/capabilities"
    respx.route(method="GET", url=ptz_url).respond(
        text=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<PTZChannelCap>"
            "<id>1</id>"
            "<ContinuousPanTiltZoom><isSupportContinuous>true</isSupportContinuous></ContinuousPanTiltZoom>"
            "</PTZChannelCap>"
        )
    )
    respx.route(method="GET", url=fixed_url).respond(
        text=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<PTZChannelCap><id>2</id></PTZChannelCap>"
        )
    )
    input_proxy_cap = f"{TEST_HOST}/ISAPI/ContentMgmt/InputProxy/channels/2/capabilities"
    respx.route(method="GET", url=input_proxy_cap).respond(status_code=404)

    client = ISAPIClient(TEST_HOST, "u1", "p1")
    await client.get_hardware_info()

    ptz_ids = {camera.id for camera in client.cameras if camera.support_ptz}
    assert 1 in ptz_ids
    assert 2 not in ptz_ids