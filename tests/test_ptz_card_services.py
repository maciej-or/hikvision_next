"""Trace card velocity controls through HA services to the NVR XML request."""

from unittest.mock import patch

import pytest
import xmltodict

from custom_components.hikvision_next.const import CONF_CONNECTION_SDK
from custom_components.hikvision_next.hikvision_device import HikvisionDevice
from custom_components.hikvision_next.isapi import IPCamera
from custom_components.hikvision_next.services import setup_services


@pytest.mark.parametrize(
    "axis,speed", [("pan", -40), ("pan", 40), ("tilt", -40), ("tilt", 40), ("zoom", -40), ("zoom", 40)]
)
async def test_card_velocities_reach_nvr_sdk_xml(hass, mock_config_entry, axis, speed):
    mock_config_entry.add_to_hass(hass)
    device = HikvisionDevice(hass, mock_config_entry)
    mock_config_entry.runtime_data = device
    device.connection_type = CONF_CONNECTION_SDK
    device.device_info.is_nvr = True
    device.cameras = [
        IPCamera(
            id=34,
            name="PTZ",
            model="iDS-2DF8C435MHR-A",
            serial_no="test",
            input_port=1,
            connection_type="Proxied",
            support_ptz=True,
        )
    ]
    setup_services(hass)
    response = b"<ResponseStatus><statusCode>1</statusCode><statusString>OK</statusString></ResponseStatus>"
    target = {"config_entry_id": mock_config_entry.entry_id, "channel_id": 34}
    with patch.object(device, "sdk_isapi", return_value=response) as sdk_xml:
        await hass.services.async_call("hikvision_next", "ptz_move", {**target, axis: speed}, blocking=True)
        method, path, body = sdk_xml.call_args.args
        assert method == "PUT"
        assert path == "/ISAPI/ContentMgmt/PTZCtrlProxy/channels/34/continuous"
        payload = xmltodict.parse(body)["PTZData"]
        assert {key: int(payload[key]) for key in ("pan", "tilt", "zoom")} == {
            key: speed if key == axis else 0 for key in ("pan", "tilt", "zoom")
        }
        await hass.services.async_call("hikvision_next", "ptz_stop", target, blocking=True)
        method, path, body = sdk_xml.call_args.args
        assert method == "PUT"
        assert path == "/ISAPI/ContentMgmt/PTZCtrlProxy/channels/34/stop"
        assert 34 not in device._ptz_active
