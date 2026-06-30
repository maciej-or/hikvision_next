"""Tests for binary sensor entity IDs."""

from homeassistant.util import slugify


def test_subscribe_sensor_unique_id_slugifies_serial_with_space():
    serial = "iDS-2CD9545-ESU 20220311AIJ64043115"
    unique_id = f"{slugify(serial.lower())}_subscribe"
    assert " " not in unique_id
    assert unique_id == "ids_2cd9545_esu_20220311aij64043115_subscribe"