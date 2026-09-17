"""Turning picked entities into the state, and refusing to guess when it cannot."""

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.jev.const import MAX_TARGET_ENTITIES
from custom_components.jev.statebuilder import async_build_state, async_entity_records


async def test_a_record_carries_what_the_question_needs(
    hass, entity_registry, device_registry, area_registry
):
    area = area_registry.async_create("Laundry room")
    entry = entity_registry.async_get_or_create(
        "sensor", "demo", "washer-power", suggested_object_id="washer_power"
    )
    entity_registry.async_update_entity(entry.entity_id, area_id=area.id)
    hass.states.async_set(
        entry.entity_id,
        "1.2",
        {
            "friendly_name": "Washer power",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
        },
    )

    [record] = async_entity_records(hass, {"entity_id": [entry.entity_id]})
    assert record["entity_id"] == entry.entity_id
    assert record["name"] == "Washer power"
    assert record["state"] == "1.2"
    assert record["unit_of_measurement"] == "W"
    assert record["device_class"] == "power"
    assert record["area"] == "Laundry room"
    assert record["changed"].endswith("ago")
    # state_class says nothing to a reader and would be billed on every evaluation
    assert "state_class" not in record


async def test_the_name_is_dropped_when_it_repeats_the_id(hass):
    hass.states.async_set("sensor.nameless", "7")
    [record] = async_entity_records(hass, {"entity_id": ["sensor.nameless"]})
    assert "name" not in record


async def test_unavailable_goes_through_untouched(hass):
    """A sensor that stopped reporting is often the answer, not a gap to fill."""
    hass.states.async_set("sensor.dead", "unavailable")
    [record] = async_entity_records(hass, {"entity_id": ["sensor.dead"]})
    assert record["state"] == "unavailable"


async def test_attributes_are_left_out_unless_asked_for(hass):
    hass.states.async_set("weather.home", "sunny", {"forecast": [{"x": 1}] * 40})
    [lean] = async_entity_records(hass, {"entity_id": ["weather.home"]})
    assert "attributes" not in lean
    [full] = async_entity_records(
        hass, {"entity_id": ["weather.home"]}, include_attributes=True
    )
    assert len(full["attributes"]["forecast"]) == 40


async def test_a_target_that_no_longer_exists_is_refused(hass):
    with pytest.raises(ServiceValidationError) as err:
        async_entity_records(hass, {"area_id": ["a-room-that-was-deleted"]})
    assert "do not exist" in str(err.value)


async def test_an_empty_target_is_refused(hass, area_registry):
    area = area_registry.async_create("Empty room")
    with pytest.raises(ServiceValidationError) as err:
        async_entity_records(hass, {"area_id": [area.id]})
    assert "no entities" in str(err.value)


async def test_the_cap_names_the_limit_and_the_ask(hass):
    over = MAX_TARGET_ENTITIES + 1
    for i in range(over):
        hass.states.async_set(f"sensor.many_{i}", "1")
    with pytest.raises(ServiceValidationError) as err:
        async_entity_records(
            hass, {"entity_id": [f"sensor.many_{i}" for i in range(over)]}
        )
    message = str(err.value)
    assert str(over) in message
    assert str(MAX_TARGET_ENTITIES) in message


async def test_text_alone_is_passed_through_unchanged(hass):
    assert async_build_state(hass, "just words", None) == "just words"


async def test_text_and_entities_become_one_object(hass):
    hass.states.async_set("sensor.watts", "1.2")
    payload = async_build_state(
        hass, "The programme finished.", {"entity_id": ["sensor.watts"]}
    )
    assert payload["note"] == "The programme finished."
    assert payload["entities"][0]["entity_id"] == "sensor.watts"
    assert "now" in payload
