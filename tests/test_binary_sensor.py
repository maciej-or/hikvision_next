"""Tests for binary sensor entity IDs."""

from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import slugify

from custom_components.hikvision_next.binary_sensor import (
    HikvisionIntercomConnectSensor,
    HikvisionSubscribeSensor,
)
from custom_components.hikvision_next.coordinator import (
    IntercomStatusCoordinator,
    SubscribeStatusCoordinator,
)


def test_connectivity_sensors_are_diagnostic():
    from unittest.mock import MagicMock

    device = MagicMock()
    device.device_info.serial_no = "DS-K1T6QT"
    device.hass_device_info.return_value = {}
    subscribe = HikvisionSubscribeSensor(SubscribeStatusCoordinator(None, device))
    intercom = HikvisionIntercomConnectSensor(IntercomStatusCoordinator(None, device))
    assert subscribe.entity_category is EntityCategory.DIAGNOSTIC
    assert intercom.entity_category is EntityCategory.DIAGNOSTIC


def test_subscribe_sensor_unique_id_slugifies_serial_with_space():
    serial = "iDS-2CD9545-ESU 20220311AIJ64043115"
    unique_id = f"{slugify(serial.lower())}_subscribe"
    assert " " not in unique_id
    assert unique_id == "ids_2cd9545_esu_20220311aij64043115_subscribe"