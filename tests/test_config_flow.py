"""Tests for the config flow."""

import respx
import pytest
from unittest.mock import patch
from custom_components.hikvision_next.const import DOMAIN
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from tests.conftest import TEST_CONFIG, TEST_HOST, load_fixture
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.mark.parametrize("mock_isapi_device", ["DS-7608NXI-I2"], indirect=True)
async def test_successful_config_flow_for_nvr(hass, mock_isapi_device):
    """Test a successful config flow."""

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=TEST_CONFIG)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == TEST_CONFIG
    assert result["title"] == "nvr"
    assert result["result"].unique_id == "DS-7608NXI-I0/0P/S0000000000CCRRJ00000000WCVU"


@pytest.mark.parametrize("mock_isapi_device", ["DS-2CD2386G2-IU"], indirect=True)
async def test_successful_config_flow_for_ipc(hass, mock_isapi_device):
    """Test a successful config flow."""

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=TEST_CONFIG)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == TEST_CONFIG
    assert result["title"] == "yard"
    assert result["result"].unique_id == "DS-2CD2386G2-IU00000000AAWRJ00000000"


@respx.mock
async def test_wrong_credentials_config_flow(hass, mock_isapi):
    """Test a config flow with wrong credentials."""

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    respx.get(f"{TEST_HOST}/ISAPI/System/deviceInfo").respond(status_code=401)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=TEST_CONFIG)
    assert result.get("type") == FlowResultType.FORM
    assert result.get("errors") == {"base": "invalid_auth"}


@patch("custom_components.hikvision_next.isapi.ISAPIClient.get_device_info")
async def test_unexpeced_exception_config_flowget_device_info_mock(get_device_info_mock, hass, mock_isapi):
    """Test a config flow with unexpeced exception."""

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    get_device_info_mock.side_effect = Exception("Something went wrong")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=TEST_CONFIG)
    assert result.get("type") == FlowResultType.FORM
    assert result.get("errors") == {"base": "Unexpected Exception: Something went wrong"}


@pytest.mark.parametrize("mock_isapi_device", ["DS-2CD2386G2-IU"], indirect=True)
async def test_user_input_validation(hass, mock_isapi_device):
    """Test a successful config flow."""

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    user_input = {
        **TEST_CONFIG,
        CONF_HOST: TEST_HOST + "/"
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=user_input)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == TEST_CONFIG


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_reconfiguration(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test reconfiguration flow."""

    entry = init_integration
    assert entry.state == ConfigEntryState.LOADED

    TEST_HOST2 = "https://1.0.0.100"
    url = f"{TEST_HOST2}/ISAPI/System/deviceInfo"
    path = "ISAPI/System.deviceInfo"
    respx.get(url).respond(text=load_fixture(path, "ipc1"))

    result = await entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **TEST_CONFIG,
            CONF_HOST: TEST_HOST2,
            CONF_VERIFY_SSL: False
        },
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_USERNAME] == TEST_CONFIG[CONF_USERNAME]
    assert entry.data[CONF_PASSWORD] == TEST_CONFIG[CONF_PASSWORD]
    assert entry.data[CONF_HOST] == TEST_HOST2
    assert entry.data[CONF_VERIFY_SSL] is False
