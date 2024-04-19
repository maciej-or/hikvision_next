"""Fixtures for testing."""

import json
import pytest
import respx
import xmltodict
from custom_components.hikvision_next.isapi import ISAPI


MOCK_HOST = "http://1.0.0.255"
MOCK_CLIENT = {
    "host": MOCK_HOST,
    "username": "u1",
    "password": "***",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def load_fixture(path, file):
    with open(f"tests/fixtures/{path}/{file}.xml", "r") as f:
        return f.read()


def mock_endpoint(endpoint, file=None, status_code=200):
    """Mock ISAPI endpoint."""

    url = f"{MOCK_HOST}/ISAPI/{endpoint}"
    path = f"ISAPI/{endpoint.replace('/', '.')}"
    if not file:
        return respx.get(url).respond(status_code=status_code)
    return respx.get(url).respond(text=load_fixture(path, file))


@pytest.fixture
def mock_isapi():
    """Mock ISAPI instance."""

    respx.get(f"{MOCK_HOST}/ISAPI/System/status").respond(status_code=200)
    isapi = ISAPI(**MOCK_CLIENT)
    return isapi


@pytest.fixture
def mock_isapi_device(request, mock_isapi):
    """Mock all device ISAPI requests."""

    model = request.param
    f = open(f"tests/fixtures/devices/{model}.json", "r")
    diagnostics = json.load(f)
    f.close()
    for endpoint in diagnostics["data"].keys():
        url = f"{MOCK_HOST}/ISAPI/{endpoint}"
        data = diagnostics["data"][endpoint]
        if status_code := data.get("status_code"):
            respx.get(url).respond(status_code=status_code)
        else:
            xml = xmltodict.unparse(data["response"])
            respx.get(url).respond(text=xml)

    return mock_isapi
