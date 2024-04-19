"""Test ISAPI responses."""

import respx
import pytest
from contextlib import suppress
from custom_components.hikvision_next.isapi import StorageInfo
from tests.conftest import mock_endpoint


@respx.mock
async def test_device_info(mock_isapi):
    isapi = mock_isapi

    mock_endpoint("System/deviceInfo", "ipc")
    await isapi.get_device_info()
    assert isapi.device_info.device_type == "IPCamera"

    mock_endpoint("System/deviceInfo", "nvr")
    await isapi.get_device_info()
    assert isapi.device_info.device_type == "NVR"


@respx.mock
async def test_storage(mock_isapi):
    isapi = mock_isapi

    mock_endpoint("ContentMgmt/Storage", "hdd1")
    storage_list = await isapi.get_storage_devices()
    assert len(storage_list) == 1
    assert storage_list[0] == StorageInfo(
        id=1, name="hdd1", type="SATA", status="ok", capacity=1907729, freespace=0, property="RW", ip=""
    )

    mock_endpoint("ContentMgmt/Storage", "hdd1_nas1")
    storage_list = await isapi.get_storage_devices()
    assert len(storage_list) == 2
    assert storage_list[0].type == "SATA"
    assert storage_list[1].type == "NFS"
    assert storage_list[1].ip != ""

    mock_endpoint("ContentMgmt/Storage", status_code=500)
    with suppress(Exception):
        storage_list = await isapi.get_storage_devices()
        assert len(storage_list) == 0


@respx.mock
@pytest.mark.parametrize("mock_isapi_device", ["DS-7608NXI-I2", "DS-2CD2386G2-IU"], indirect=True)
async def test_all_devices(mock_isapi_device):
    isapi = mock_isapi_device

    storage_list = await isapi.get_storage_devices()
    assert len(storage_list) > 0

    await isapi.get_protocols()
    assert isapi.device_info.rtsp_port == "10554"

    alarm_server = await isapi.get_alarm_server()
    assert alarm_server.ipAddress is not None


@respx.mock
@pytest.mark.parametrize("mock_isapi_device", ["DS-7608NXI-I2"], indirect=True)
async def test_nvr(mock_isapi_device):
    isapi = mock_isapi_device

    await isapi.get_hardware_info()
    assert isapi.device_info.device_type == "NVR"
    assert isapi.device_info.is_nvr is True

    device_info = isapi.hass_device_info()
    assert "via_device" not in device_info


@respx.mock
@pytest.mark.parametrize("mock_isapi_device", ["DS-2CD2386G2-IU"], indirect=True)
async def test_standalone_ip_camera(mock_isapi_device):
    isapi = mock_isapi_device

    await isapi.get_hardware_info()
    assert isapi.device_info.device_type == "IPCamera"
    assert isapi.device_info.is_nvr is False

    device_info = isapi.hass_device_info()
    assert "via_device" not in device_info
