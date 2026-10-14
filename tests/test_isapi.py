"""Tests for specific ISAPI responses."""

from contextlib import suppress

import httpx
import respx

from custom_components.hikvision_next.isapi import ISAPIClient, Partition, StorageInfo, Zone
from tests.conftest import load_fixture, mock_endpoint


@respx.mock
async def test_storage(mock_isapi):
    isapi = mock_isapi

    mock_endpoint("ContentMgmt/Storage", "hdd1")
    storage_list = await isapi.get_storage_devices()
    assert len(storage_list) == 1
    assert storage_list[0] == StorageInfo(
        id=1,
        name="hdd1",
        type="SATA",
        status="ok",
        capacity=1907729,
        freespace=0,
        property="RW",
        ip="",
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
async def test_notification_hosts(mock_isapi):
    isapi = mock_isapi

    mock_endpoint("Event/notification/httpHosts", "nvr_single_item")
    host_nvr = await isapi.get_alarm_server()

    mock_endpoint("Event/notification/httpHosts", "ipc_list")
    host_ipc = await isapi.get_alarm_server()

    assert host_nvr == host_ipc


@respx.mock
async def test_update_notification_hosts(mock_isapi):
    isapi = mock_isapi

    def update_side_effect(request, route):
        payload = load_fixture("ISAPI/Event.notification.httpHosts", "set_alarm_server_payload")
        if request.content.decode("utf-8") != payload:
            raise AssertionError("Request content does not match expected payload")
        return httpx.Response(200)

    mock_endpoint("Event/notification/httpHosts", "nvr_single_item")
    url = f"{isapi.host}/ISAPI/Event/notification/httpHosts"
    endpoint = respx.put(url).mock(side_effect=update_side_effect)
    await isapi.set_alarm_server("http://1.0.0.11:8123", "/api/hikvision")

    assert endpoint.called


@respx.mock
async def test_update_notification_hosts_from_ipaddress_to_hostname(mock_isapi):
    isapi = mock_isapi

    def update_side_effect(request, route):
        payload = load_fixture("ISAPI/Event.notification.httpHosts", "set_alarm_server_outside_network_payload")
        if request.content.decode("utf-8") != payload:
            raise AssertionError("Request content does not match expected payload")
        return httpx.Response(200)

    mock_endpoint("Event/notification/httpHosts", "nvr_single_item")
    url = f"{isapi.host}/ISAPI/Event/notification/httpHosts"
    endpoint = respx.put(url).mock(side_effect=update_side_effect)
    await isapi.set_alarm_server("https://ha.hostname.domain", "/api/hikvision")

    assert endpoint.called


@respx.mock
async def test_get_partitions(mock_isapi):
    isapi = mock_isapi

    mock_endpoint("SecurityCP/Configuration/subSys", "subsys_2_partitions", ext="json")
    mock_endpoint("SecurityCP/status/subSystems", "status_2_partitions", ext="json")

    partitions = await isapi.get_partitions()

    assert partitions == [
        Partition(id=1, name="Home", enabled=True, arming="away", alarm=False, delay_time=0),
        Partition(id=2, name="Garage", enabled=True, arming="disarm", alarm=False, delay_time=0),
    ]


@respx.mock
async def test_get_partitions_skips_disabled(mock_isapi):
    """The panel always reports every possible partition slot regardless of how many are
    actually configured/in use -- get_partitions() should skip the disabled ones rather than
    returning an entry (and eventually an alarm_control_panel entity) for every unused slot.
    """
    isapi = mock_isapi

    mock_endpoint("SecurityCP/Configuration/subSys", "subsys_with_disabled", ext="json")
    mock_endpoint("SecurityCP/status/subSystems", "status_with_disabled", ext="json")

    partitions = await isapi.get_partitions()

    assert partitions == [
        Partition(id=1, name="Home", enabled=True, arming="away", alarm=False, delay_time=0),
    ]


@respx.mock
async def test_get_zones(mock_isapi):
    isapi = mock_isapi

    mock_endpoint("SecurityCP/Configuration/zones", "zones_3", ext="json")
    mock_endpoint("SecurityCP/status/zones", "status_3_zones", ext="json")

    zones = await isapi.get_zones()

    assert zones == [
        Zone(
            id=1,
            name="Front Door",
            partition_id=1,
            detector_type="magneticContact",
            zone_type="Instant",
            status="online",
            alarm=False,
            bypassed=False,
            tamper_evident=False,
            armed=True,
            charge="normal",
            magnet_open_status=True,
        ),
        Zone(
            id=2,
            name="Living Room PIR",
            partition_id=1,
            detector_type="passiveInfraredDetector",
            zone_type="Delay",
            status="trigger",
            alarm=True,
            bypassed=False,
            tamper_evident=False,
            armed=True,
            charge="normal",
        ),
        Zone(
            id=3,
            name="Garage Smoke",
            partition_id=2,
            detector_type="smokeDetector",
            zone_type="24hSound",
            status="online",
            alarm=False,
            bypassed=True,
            tamper_evident=False,
            armed=False,
            charge="lowPower",
        ),
    ]


@respx.mock
async def test_arm_disarm_partition(mock_isapi):
    isapi = mock_isapi

    url = f"{isapi.host}/ISAPI/SecurityCP/control/arm/1"
    endpoint = respx.put(url, params={"ways": "away"}).respond(200)
    await isapi.arm_partition(1, "away")
    assert endpoint.called

    url = f"{isapi.host}/ISAPI/SecurityCP/control/disarm/1"
    endpoint = respx.put(url).respond(200)
    await isapi.disarm_partition(1)
    assert endpoint.called

    url = f"{isapi.host}/ISAPI/SecurityCP/control/clearAlarm/1"
    endpoint = respx.put(url).respond(200)
    await isapi.clear_partition_alarm(1)
    assert endpoint.called


def test_decode_arming_part_falls_back_on_invalid_utf8():
    """A zone/partition name with accented characters can arrive mis-encoded (not UTF-8).

    The device's Content-Type claims charset="UTF-8", but some firmware embeds
    user-configured names (e.g. "Área") using the device's own locale codepage instead. This
    must not raise -- the field should decode via a fallback encoding rather than the whole
    event being silently dropped.
    """
    valid_utf8 = b'{"zoneName": "Zona 3"}'
    assert ISAPIClient._decode_arming_part(valid_utf8) == '{"zoneName": "Zona 3"}'

    # "Área" encoded as Latin-1 is not valid UTF-8 (0xC1 is not a valid UTF-8 lead byte here)
    mis_encoded = '{"zoneName": "Área"}'.encode("latin-1")
    assert ISAPIClient._decode_arming_part(mis_encoded) == '{"zoneName": "Área"}'
