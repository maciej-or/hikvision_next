"""Tests for image platform."""

import pytest
from homeassistant.components.image import DATA_COMPONENT, DOMAIN as IMAGE_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, CONF_FILENAME, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
import homeassistant.helpers.entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_next.const import ACTION_UPDATE_SNAPSHOT, DOMAIN

SNAPSHOT_ENTITIES = [
    "image.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_101_snapshot",
    "image.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_201_snapshot",
    "image.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_301_snapshot",
]


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_snapshot_entities(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test snapshot entities are registered in the image domain."""

    entity_registry = er.async_get(hass)

    assert sorted(hass.states.async_entity_ids(IMAGE_DOMAIN)) == SNAPSHOT_ENTITIES

    for entity_id in SNAPSHOT_ENTITIES:
        assert (entity := entity_registry.async_get(entity_id))
        assert entity.platform == DOMAIN

    # snapshots used to set a camera. entity_id, see issues #365, #366, #368
    assert not [
        entity_id for entity_id in hass.states.async_entity_ids("camera") if entity_id.endswith("_snapshot")
    ]


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_update_snapshot_action(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test the update_snapshot action renders the filename template.

    The action targets the image domain, see services.yaml and the
    take_pictures_on_motion_detection blueprint.
    """

    entity_id = SNAPSHOT_ENTITIES[0]
    assert hass.states.get(entity_id).state == STATE_UNKNOWN

    await hass.services.async_call(
        DOMAIN,
        ACTION_UPDATE_SNAPSHOT,
        {ATTR_ENTITY_ID: entity_id, CONF_FILENAME: "/media/{{ entity_id }}.jpg"},
        blocking=True,
    )
    await hass.async_block_till_done()

    entity = hass.data[DATA_COMPONENT].get_entity(entity_id)
    assert entity.file_path == f"/media/{entity_id}.jpg"
    assert hass.states.get(entity_id).state != STATE_UNKNOWN
