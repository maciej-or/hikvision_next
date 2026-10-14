"""Tests for zone detector type -> device_class mapping."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.hikvision_next.const import ZONE_DEVICE_CLASS


def test_vibration_detector_maps_to_vibration_device_class():
    assert ZONE_DEVICE_CLASS["vibrationDetector"] == BinarySensorDeviceClass.VIBRATION


def test_magnetic_contact_maps_to_door_device_class():
    assert ZONE_DEVICE_CLASS["magneticContact"] == BinarySensorDeviceClass.DOOR


def test_humidity_detector_maps_to_moisture_device_class():
    assert ZONE_DEVICE_CLASS["humidityDetector"] == BinarySensorDeviceClass.MOISTURE


def test_tamper_detector_maps_to_tamper_device_class():
    assert ZONE_DEVICE_CLASS["tamperDetector"] == BinarySensorDeviceClass.TAMPER
