import respx
import pytest
from custom_components.hikvision_next.const import DOMAIN
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.config_entries import SOURCE_USER
from tests.conftest import MOCK_CLIENT

MOCK_CONFIG = {**MOCK_CLIENT, "set_alarm_server": False, "alarm_server": ""}


@respx.mock
@pytest.mark.parametrize("mock_isapi_device", ["DS-7608NXI-I2"], indirect=True)
async def test_successful_config_flow_for_nvr(hass, mock_isapi_device):
    """Test a successful config flow."""

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=MOCK_CONFIG)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == MOCK_CONFIG
    assert result["title"] == "nvr"
    assert result["result"].unique_id == "DS-7608NXI-I0/0P/S0000000000CCRRJ00000000WCVU"


@respx.mock
@pytest.mark.parametrize("mock_isapi_device", ["DS-2CD2386G2-IU"], indirect=True)
async def test_successful_config_flow_for_ipc(hass, mock_isapi_device):
    """Test a successful config flow."""

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=MOCK_CONFIG)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == MOCK_CONFIG
    assert result["title"] == "yard"
    assert result["result"].unique_id == "DS-2CD2386G2-IU00000000AAWRJ00000000"
