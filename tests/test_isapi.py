"""Tests for specific ISAPI responses."""

from contextlib import suppress
from unittest.mock import AsyncMock

import httpx
import respx

from custom_components.hikvision_next.isapi import StorageInfo
from tests.conftest import mock_endpoint, load_fixture


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


async def test_single_channel_event_fallback(mock_isapi):
    """Test event discovery when a single-channel camera omits Event/triggers."""
    responses = {
        "Event/triggers": {},
        "Event/channels/capabilities": {
            "ChannelEventCapList": {
                "ChannelEventCap": {
                    "eventType": {"@opt": "fielddetection"},
                    "channelID": "1",
                }
            }
        },
        "Event/triggers/fielddetection-1": {
            "EventTrigger": {
                "eventType": "fielddetection",
                "videoInputChannelID": "1",
                "EventTriggerNotificationList": {
                    "EventTriggerNotification": {
                        "notificationMethod": "center",
                    }
                },
            }
        },
    }
    mock_isapi.request = AsyncMock(side_effect=lambda _, endpoint: responses[endpoint])

    events = await mock_isapi.get_supported_events({})

    assert mock_isapi.capabilities.is_multi_channel is False
    assert [(event.id, event.channel_id, event.notifications) for event in events] == [
        ("fielddetection", 1, ["center"])
    ]
    assert [awaited.args[1] for awaited in mock_isapi.request.await_args_list] == [
        "Event/triggers",
        "Event/channels/capabilities",
        "Event/triggers/fielddetection-1",
    ]


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
