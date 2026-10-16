"""Tests for SDK utility helpers."""

from custom_components.hikvision_next.sdk.utils import map_vca_rule_event


def test_map_vca_rule_event_dw_event_type():
    assert map_vca_rule_event(0, 0x1) == "linedetection"
    assert map_vca_rule_event(0, 0x2) == "regionentrance"
    assert map_vca_rule_event(0, 0x4) == "regionexiting"
    assert map_vca_rule_event(0, 0x8) == "fielddetection"


def test_map_vca_rule_event_w_event_type_ex():
    assert map_vca_rule_event(1, 0) == "linedetection"
    assert map_vca_rule_event(2, 0) == "regionentrance"
    assert map_vca_rule_event(3, 0) == "regionexiting"
    assert map_vca_rule_event(4, 0) == "fielddetection"


def test_map_vca_rule_event_unknown():
    assert map_vca_rule_event(0, 0x10) is None
    assert map_vca_rule_event(99, 0) is None


def test_sdk_put_reads_status_buffer_even_when_successful():
    from ctypes import memmove
    from types import SimpleNamespace

    from custom_components.hikvision_next.sdk.utils import call_ISAPI

    response = b"<ResponseStatus><statusCode>1</statusCode></ResponseStatus>"

    def native(user, request, output):
        # Real NVR behavior: reported length, zero body, populated status.
        memmove(output.lpStatusBuffer, response, len(response))
        output.dwReturnedXMLSize = len(response)
        return 1

    sdk = SimpleNamespace(NET_DVR_STDXMLConfig=native)
    assert call_ISAPI(sdk, 0, "PUT", "/ISAPI/test", "<PTZData/>") == response


def test_sdk_xml_get_prefers_data_and_accepts_empty_input():
    from ctypes import memmove
    from types import SimpleNamespace

    from custom_components.hikvision_next.sdk.utils import call_ISAPI

    response = b"<Capabilities><id>34</id></Capabilities>"

    def native(user, request, output):
        assert request.dwInBufferSize == 0
        memmove(output.lpOutBuffer, response, len(response))
        output.dwReturnedXMLSize = len(response)
        return 1

    sdk = SimpleNamespace(NET_DVR_STDXMLConfig=native)
    assert call_ISAPI(sdk, 0, "GET", "/ISAPI/test", None) == response


def test_sdk_put_returns_rejected_status_instead_of_nul_bytes():
    from ctypes import memmove
    from types import SimpleNamespace

    from custom_components.hikvision_next.sdk.utils import call_ISAPI

    response = (
        b"<ResponseStatus><statusCode>4</statusCode><subStatusCode>badXmlContent</subStatusCode></ResponseStatus>"
    )

    def native(user, request, output):
        memmove(output.lpStatusBuffer, response, len(response))
        output.dwReturnedXMLSize = len(response)
        return 1

    assert call_ISAPI(SimpleNamespace(NET_DVR_STDXMLConfig=native), 0, "PUT", "/ISAPI/test") == response
