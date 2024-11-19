"""Fixtures for testing."""

import json
import pytest
import respx
import xmltodict
from custom_components.hikvision_next.const import DOMAIN, CONF_SET_ALARM_SERVER, CONF_ALARM_SERVER_HOST, FORCE_RTSP_PORT, RTSP_PORT_FORCED
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.hikvision_next.isapi import ISAPIClient
from homeassistant.core import HomeAssistant

TEST_HOST_IP = "1.0.0.255"
TEST_HOST = f"http://{TEST_HOST_IP}"
TEST_CLIENT = {
    CONF_HOST: TEST_HOST,
    CONF_USERNAME: "u1",
    CONF_PASSWORD: "***",
    FORCE_RTSP_PORT: False,
    RTSP_PORT_FORCED: 554,
}

TEST_CLIENT_OUTSIDE_NETWORK = {
    CONF_HOST: "https://address.domain",
    CONF_USERNAME: "u1",
    CONF_PASSWORD: "***",
    FORCE_RTSP_PORT: True,
    RTSP_PORT_FORCED: 5151, 
    CONF_SET_ALARM_SERVER: False, 
    CONF_ALARM_SERVER_HOST: ""
}

TEST_CONFIG = {**TEST_CLIENT, CONF_VERIFY_SSL: True, CONF_SET_ALARM_SERVER: False, CONF_ALARM_SERVER_HOST: ""}
TEST_CONFIG_WITH_ALARM_SERVER = {
    **TEST_CLIENT,
    CONF_VERIFY_SSL: True,
    CONF_SET_ALARM_SERVER: True,
    CONF_ALARM_SERVER_HOST: "http://1.0.0.11:8123",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def mock_config_entry(request) -> MockConfigEntry:
    """Return the default mocked config entry."""

    config = getattr(request, "param", TEST_CONFIG)
    return MockConfigEntry(
        domain=DOMAIN,
        data=config,
        version=2
    )


def load_fixture(path, file):
    with open(f"tests/fixtures/{path}/{file}.xml", "r") as f:
        return f.read()


def mock_endpoint(endpoint, file=None, status_code=200):
    """Mock ISAPI endpoint."""

    url = f"{TEST_HOST}/ISAPI/{endpoint}"
    path = f"ISAPI/{endpoint.replace('/', '.')}"
    if not file:
        return respx.get(url).respond(status_code=status_code)
    return respx.get(url).respond(text=load_fixture(path, file))

def mock_device_endpoints(model, base_url):
    """Mock all ISAPI requests used for device initialization."""

    if not base_url:
        base_url=TEST_HOST

    f = open(f"tests/fixtures/devices/{model}.json", "r")
    diagnostics = json.load(f)
    f.close()
    for endpoint, data in diagnostics["data"]["ISAPI"].items():
        url = f"{base_url}/ISAPI/{endpoint}"
        if status_code := data.get("status_code"):
            respx.get(url).respond(status_code=status_code)
        elif response := data.get("response"):
            xml = xmltodict.unparse(response)
            respx.get(url).respond(text=xml)


@pytest.fixture
def mock_isapi(respx_mock):
    """Mock ISAPI instance."""

    digest_header = 'Digest realm="testrealm", qop="auth", nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093", opaque="799d5"'
    respx.get(f"{TEST_HOST}/ISAPI/System/deviceInfo").respond(
        status_code=401, headers={"WWW-Authenticate": digest_header}
    )
    isapi = ISAPIClient(**TEST_CLIENT)
    return isapi


@pytest.fixture
def mock_isapi_device(respx_mock, request, mock_isapi):
    """Mock all device ISAPI requests."""

    model = request.param
    mock_device_endpoints(model, TEST_HOST)
    return mock_isapi


@pytest.fixture
async def init_integration(respx_mock, request, mock_isapi, hass: HomeAssistant, mock_config_entry: MockConfigEntry):
    """
    Mock integration in device context.

    :param request: model or (model, skip_setup)
        model - fixtures/devices subfolder
        skip_setup - default False, if True skips setup of the integration
    """
    return await load_an_integ(request.param, hass, mock_config_entry)


@pytest.fixture
async def init_integration_outside_network(respx_mock, request, mock_isapi, hass: HomeAssistant, mock_config_entry: MockConfigEntry):
    """
    Mock integration in device context.

    :param request: model or (model, skip_setup)
        model - fixtures/devices subfolder
        skip_setup - default False, if True skips setup of the integration
    """
    entry= MockConfigEntry(
        domain=DOMAIN,
        data=TEST_CLIENT_OUTSIDE_NETWORK,
        version=2
    )
    return await load_an_integ(request.param, hass, entry)


async def load_an_integ(model, hass: HomeAssistant, mock_config_entry: MockConfigEntry):
    # async def init_integration(respx_mock, request, mock_isapi, hass: HomeAssistant, mock_config_entry: MockConfigEntry):
    """
    Mock integration in device context.

    :param request: model or (model, skip_setup)
        model - fixtures/devices subfolder
        skip_setup - default False, if True skips setup of the integration
    """

    skip_setup = False
    if len(model) == 2:
        model = model[0]
        skip_setup = model[1]
    
    base_url=mock_config_entry.data.get('host')

    mock_device_endpoints(model, base_url)

    mock_config_entry.add_to_hass(hass)

    if not skip_setup:
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        unique_id = None
        if mock_config_entry.state.value == 'loaded':
            unique_id = mock_config_entry.runtime_data.device_info.serial_no
        hass.config_entries.async_update_entry(
            mock_config_entry,
            data={**mock_config_entry.data},
            title=model,
            unique_id=unique_id,
        )

    return mock_config_entry
