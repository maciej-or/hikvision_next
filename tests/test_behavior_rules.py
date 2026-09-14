"""Behavior rule discovery, switches and diagnostic fixtures."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
import xmltodict
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.hikvision_next.const import BEHAVIOR_RULES_COORDINATOR, DOMAIN
from custom_components.hikvision_next.diagnostics import async_get_config_entry_diagnostics
from custom_components.hikvision_next.isapi import BehaviorRuleInfo, ISAPIForbiddenError
from tests.conftest import TEST_HOST

MODEL = "DS-2TD1228-3-QA"
RULE_ENDPOINT = "Intelligent/channels/2/behaviorRule/1"
ENTITY_PREFIX = "switch.ds_2td1228_3_qa00000000acwr000000000_2"


def device_responses():
    """Load sanitized responses captured from a camera with two behavior rules."""
    return json.loads(Path(f"tests/fixtures/devices/{MODEL}.json").read_text())["data"]["ISAPI"]


def mock_response(endpoint, response):
    """Serve a recorded XML response."""
    return respx.get(f"{TEST_HOST}/ISAPI/{endpoint}").respond(text=xmltodict.unparse(response))


@pytest.mark.parametrize("single_rule", [False, True])
async def test_discover_behavior_rules(mock_isapi, single_rule):
    """Parse both repeated RuleInfo elements and a single XML element."""
    responses = device_responses()
    mock_isapi.cameras = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    for channel in (1, 2):
        endpoint = f"Event/channels/{channel}/capabilities"
        mock_response(endpoint, responses[endpoint]["response"])
    response = responses[RULE_ENDPOINT]["response"]
    if single_rule:
        response["BehaviorRule"]["RuleInfoList"]["RuleInfo"] = response["BehaviorRule"]["RuleInfoList"]["RuleInfo"][0]
    mock_response(RULE_ENDPOINT, response)

    rules = await mock_isapi.get_behavior_rules()

    assert len(rules) == (1 if single_rule else 2)
    assert rules[0] == BehaviorRuleInfo(1, 2, "Rule 1", "fielddetection", "region", False)
    if not single_rule:
        assert rules[1] == BehaviorRuleInfo(2, 2, "Rule 2", "fielddetection", "region", False)
    # No behavior endpoint is mocked on channel 1: its capabilities do not advertise field detection.


@pytest.mark.parametrize("rule_info", [None, {}, {"ruleId": "1"}, {"ruleName": "Incomplete"}])
async def test_empty_or_incomplete_rules(mock_isapi, rule_info):
    """Empty XML containers and incomplete rules do not prevent initialization."""
    mock_isapi.cameras = [SimpleNamespace(id=2)]
    responses = device_responses()
    endpoint = "Event/channels/2/capabilities"
    mock_response(endpoint, responses[endpoint]["response"])
    response = responses[RULE_ENDPOINT]["response"]
    response["BehaviorRule"]["RuleInfoList"]["RuleInfo"] = rule_info
    mock_response(RULE_ENDPOINT, response)
    assert await mock_isapi.get_behavior_rules() == []


@pytest.mark.parametrize(
    "forbidden_endpoint", ["Event/channels/1/capabilities", "Intelligent/channels/1/behaviorRule/1"]
)
async def test_unsupported_channel_does_not_block_refresh(mock_isapi, forbidden_endpoint):
    """A 403 (including a cached 403) on one channel leaves other rules readable."""
    mock_isapi.cameras = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    responses = device_responses()
    for channel in (1, 2):
        mock_response(f"Event/channels/{channel}/capabilities", responses["Event/channels/2/capabilities"]["response"])
    forbidden = respx.get(f"{TEST_HOST}/ISAPI/{forbidden_endpoint}").respond(403)
    mock_response(RULE_ENDPOINT, responses[RULE_ENDPOINT]["response"])

    for _ in range(2):
        rules = await mock_isapi.get_behavior_rules()
        assert [rule.channel_id for rule in rules] == [2, 2]
    assert forbidden.call_count == 1


@pytest.mark.parametrize("init_integration", [MODEL], indirect=True)
async def test_behavior_switch_polling_and_commands(hass, init_integration):
    """Switches use the polled state and send on/off commands for the selected rule."""
    device = init_integration.runtime_data
    coordinator = device.coordinators[BEHAVIOR_RULES_COORDINATOR]
    entity_id = f"{ENTITY_PREFIX}_1_behavior_rule"
    other_entity = f"{ENTITY_PREFIX}_2_behavior_rule"
    assert hass.states.get(entity_id).state == STATE_OFF
    assert hass.states.get(entity_id).attributes["friendly_name"].endswith("Rule 1")
    assert hass.states.get(other_entity).state == STATE_OFF
    response = device_responses()[RULE_ENDPOINT]["response"]
    current_rule = response["BehaviorRule"]["RuleInfoList"]["RuleInfo"][0]
    other_rule = deepcopy(response["BehaviorRule"]["RuleInfoList"]["RuleInfo"][1])

    def update_rule(request):
        payload = xmltodict.parse(request.content)["BehaviorRule"]
        assert payload["sid"] == "1"
        rule = payload["RuleInfoList"]["RuleInfo"]
        assert rule["ruleId"] == "1"
        assert rule["ruleName"] == "Rule 1"
        assert set(rule) == {"ruleId", "ruleName", "enabled"}
        assert rule["enabled"] in ("true", "false")
        current_rule["enabled"] = rule["enabled"]
        mock_response(RULE_ENDPOINT, response)
        return httpx.Response(200, text="<ResponseStatus><statusCode>1</statusCode></ResponseStatus>")

    command = respx.put(f"{TEST_HOST}/ISAPI/{RULE_ENDPOINT}").mock(side_effect=update_rule)
    for service, expected in ((SERVICE_TURN_ON, STATE_ON), (SERVICE_TURN_OFF, STATE_OFF)):
        await hass.services.async_call(SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: entity_id}, blocking=True)
        # Consecutive commands can share the coordinator's debounce window.
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == expected
        assert hass.states.get(other_entity).state == STATE_OFF
        assert response["BehaviorRule"]["RuleInfoList"]["RuleInfo"][1] == other_rule
    assert command.call_count == 2

    # An external camera change must also be reflected, without a switch command.
    current_rule["enabled"] = "true"
    mock_response(RULE_ENDPOINT, response)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_ON
    assert command.call_count == 2

    coordinator.async_set_updated_data({})
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNKNOWN


@pytest.mark.parametrize("init_integration", [("DS-7608NXI-I2", True)], indirect=True)
async def test_behavior_switch_belongs_to_its_channel(hass, init_integration):
    """A rule on the first NVR channel must not attach to the last camera."""
    responses = device_responses()
    mock_response("Event/channels/1/capabilities", responses["Event/channels/2/capabilities"]["response"])
    mock_response("Intelligent/channels/1/behaviorRule/1", responses[RULE_ENDPOINT]["response"])
    assert await hass.config_entries.async_setup(init_integration.entry_id)
    await hass.async_block_till_done()

    device = init_integration.runtime_data
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    rule_entities = [entry for entry in entities.entities.values() if entry.unique_id.endswith("_behavior_rule")]
    assert len(rule_entities) == 2
    expected = devices.async_get_device(identifiers={(DOMAIN, device.cameras[0].serial_no)})
    assert device.cameras[0].serial_no != device.cameras[-1].serial_no
    assert all(entry.device_id == expected.id for entry in rule_entities)
    assert all(hass.states.get(entry.entity_id).state == STATE_OFF for entry in rule_entities)


async def test_behavior_command_retries_after_forbidden(mock_isapi):
    """Explicit user commands bypass the background polling forbidden cache."""
    endpoint = respx.put(f"{TEST_HOST}/ISAPI/{RULE_ENDPOINT}").respond(403)
    rule = BehaviorRuleInfo(1, 2, "Rule 1", "fielddetection", "region", False)
    with pytest.raises(ISAPIForbiddenError):
        await mock_isapi.set_behavior_rule_enabled(rule, True)
    endpoint.respond(200, text="<ResponseStatus><statusCode>1</statusCode></ResponseStatus>")
    await mock_isapi.set_behavior_rule_enabled(rule, True)
    assert endpoint.call_count == 2


@pytest.mark.parametrize("init_integration", [MODEL], indirect=True)
async def test_behavior_diagnostics(hass, init_integration):
    """Diagnostics include the new endpoints so future device fixtures are complete."""
    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)
    for channel in (1, 2):
        assert f"Event/channels/{channel}/capabilities" in diagnostics["ISAPI"]
        assert f"Intelligent/channels/{channel}/behaviorRule/1" in diagnostics["ISAPI"]
    assert diagnostics["ISAPI"][RULE_ENDPOINT] == device_responses()[RULE_ENDPOINT]
    assert not {endpoint: value for endpoint, value in diagnostics["ISAPI"].items() if "error" in value}
