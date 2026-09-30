"""Which entities the cap keeps when a house has more than the agent may send."""

import pytest
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.setup import async_setup_component

from custom_components.jev.rank import bm25, words
from custom_components.jev.snapshot import async_snapshot


def test_words_are_case_folded_and_split_on_anything_but_letters():
    assert words("Turn ON the Desk-Lamp, please!") == [
        "turn",
        "on",
        "the",
        "desk",
        "lamp",
        "please",
    ]


def test_a_script_without_spaces_counts_each_character():
    """Otherwise the whole sentence would be one word that no name equals."""
    assert words("打开台灯") == ["打", "开", "台", "灯"]
    assert words("台灯 Lamp") == ["台", "灯", "lamp"]


def test_a_rare_word_outweighs_a_word_every_entity_has():
    documents = [["zebra", "light"], ["kitchen", "light"], ["office", "light"]]
    scores = bm25(words("turn on the zebra light"), documents)
    assert scores[0] > scores[1] == scores[2] > 0


def test_a_query_that_shares_no_word_scores_nothing():
    assert bm25(["hello"], [["lamp"], ["fan"]]) == [0.0, 0.0]
    assert bm25(["hello"], []) == []


@pytest.fixture
async def big_house(hass):
    """Ten lights in the Attic upstairs, then a Zebra lamp in the Den downstairs."""
    assert await async_setup_component(hass, "homeassistant", {})
    areas = ar.async_get(hass)
    floors = fr.async_get(hass)
    upstairs = floors.async_create("Upstairs", aliases={"top floor"})
    downstairs = floors.async_create("Downstairs", aliases={"ground floor"})
    attic = areas.async_create("Attic", floor_id=upstairs.floor_id)
    den = areas.async_create("Den", aliases={"snug"}, floor_id=downstairs.floor_id)
    registry = er.async_get(hass)
    lights = [(f"light.a{i:02}", f"Attic light {i}", attic) for i in range(10)]
    for entity_id, name, area in [*lights, ("light.zebra_lamp", "Zebra lamp", den)]:
        object_id = entity_id.split(".")[1]
        entry = registry.async_get_or_create(
            "light", "demo", object_id, suggested_object_id=object_id
        )
        registry.async_update_entity(entry.entity_id, name=name, area_id=area.id)
        hass.states.async_set(entity_id, "off", {"friendly_name": name})
        async_expose_entity(hass, conversation.DOMAIN, entity_id, True)
    return {"attic": attic, "den": den}


def kept(snapshot):
    return [e.entity_id for e in snapshot.entities]


async def test_the_entity_the_command_names_is_kept_past_the_cap(hass, big_house):
    snapshot = async_snapshot(hass, 5, "turn on the zebra lamp")

    assert "light.zebra_lamp" in kept(snapshot)
    assert snapshot.left_out == 6
    # The option list keeps its entity_id order, so a trace reads the same twice.
    assert kept(snapshot) == sorted(kept(snapshot))
    assert "Zebra lamp" not in snapshot.hidden_names


async def test_an_area_alias_brings_its_room_in(hass, big_house):
    snapshot = async_snapshot(hass, 5, "lights on in the snug")
    assert "light.zebra_lamp" in kept(snapshot)
    assert snapshot.areas == ["Attic", "Den"]


async def test_a_floor_alias_brings_its_rooms_in(hass, big_house):
    """Both floors say "floor", so only "ground" picks the Den."""
    snapshot = async_snapshot(hass, 5, "everything off on the ground floor")
    assert "light.zebra_lamp" in kept(snapshot)
    assert snapshot.floors == ["Downstairs", "Upstairs"]


async def test_the_room_that_heard_it_comes_before_entity_id(hass, big_house):
    """A command that names nothing keeps the satellite's room first."""
    snapshot = async_snapshot(hass, 5, "turn it on", big_house["den"].id)
    assert "light.zebra_lamp" in kept(snapshot)


async def test_a_named_entity_beats_the_room_that_heard_it(hass, big_house):
    snapshot = async_snapshot(hass, 1, "attic light 7", big_house["den"].id)
    assert kept(snapshot) == ["light.a07"]


async def test_without_a_command_the_first_by_entity_id_are_kept(hass, big_house):
    snapshot = async_snapshot(hass, 5)
    assert kept(snapshot) == [f"light.a{i:02}" for i in range(5)]
    assert "Zebra lamp" in snapshot.hidden_names


async def test_under_the_cap_nothing_is_left_out(hass, big_house):
    snapshot = async_snapshot(hass, 150, "turn on the zebra lamp")
    assert len(kept(snapshot)) == 11
    assert snapshot.left_out == 0
