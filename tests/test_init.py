"""Contexts, the entities they produce, and the budget that stops them."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_API_KEY
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from jevclient import NoulAnswer
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.jev.const import CONF_DAILY_TOKEN_BUDGET, DOMAIN

from .conftest import build_response

CONTEXT = {
    "name": "Laundry",
    "scan_interval": 300,
    "entities": ["sensor.washer_power"],
    "questions": [
        {
            "name": "Laundry forgotten",
            "type": "noul",
            "instructions": "Is the laundry finished but still in the machine?",
            "threshold": 0.7,
        }
    ],
}


async def setup_with_context(hass, config_entry, context=None):
    hass.states.async_set("sensor.washer_power", "1.2", {"friendly_name": "Washer power"})
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: [context or CONTEXT]})
    if config_entry.entry_id not in hass.config_entries._entries:
        config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_a_context_makes_a_sensor_per_question(hass, mock_client, config_entry):
    mock_client.ask.return_value = build_response(
        laundry_laundry_forgotten=NoulAnswer(noul=0.81)
    )
    await setup_with_context(hass, config_entry)

    assert hass.states.get("sensor.jev_laundry_forgotten").state == "0.81"
    # A threshold turns the same answer into something an automation can trigger on.
    assert hass.states.get("binary_sensor.jev_laundry_forgotten").state == "on"
    assert hass.states.get("sensor.jev_input_tokens_today").state == "321"
    assert hass.states.get("sensor.jev_calls_today").state == "1"


async def test_the_state_is_built_from_the_named_entities(hass, mock_client, config_entry):
    await setup_with_context(hass, config_entry)
    sent = mock_client.ask.await_args.args[0]
    assert sent["entities"][0]["name"] == "Washer power"
    assert sent["entities"][0]["state"] == "1.2"


async def test_a_context_needs_something_to_look_at(hass, mock_client, config_entry):
    """Neither entities nor state means there is nothing to judge."""
    bad = {k: v for k, v in CONTEXT.items() if k != "entities"}
    assert not await async_setup_component(hass, DOMAIN, {DOMAIN: [bad]})


@pytest.mark.parametrize(
    ("levels", "fragment"),
    [(["only one"], "2 to 10 levels"), ([str(i) for i in range(11)], "2 to 10 levels")],
)
async def test_yaml_refuses_the_same_limits_as_the_actions(
    hass, mock_client, config_entry, levels, fragment
):
    bad = {
        **CONTEXT,
        "questions": [
            {"name": "Urgency", "type": "score", "instructions": "How urgent?",
             "criteria": levels}
        ],
    }
    assert not await async_setup_component(hass, DOMAIN, {DOMAIN: [bad]})


async def test_the_budget_stops_evaluation_and_says_why(hass, mock_client):
    # Built with the budget already set: updating options on a live entry reloads it,
    # which is a different thing to test.
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Jev",
        data={CONF_API_KEY: "test-key-not-a-real-one"},
        options={CONF_DAILY_TOKEN_BUDGET: 100},
        unique_id="budget-entry",
    )
    await setup_with_context(hass, entry)

    # The first call spends 321 tokens, which is already past the budget of 100.
    assert hass.states.get("sensor.jev_input_tokens_today").state == "321"
    calls_before = mock_client.ask.await_count

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=310))
    await hass.async_block_till_done()

    assert mock_client.ask.await_count == calls_before, "it kept spending past the budget"
    assert hass.states.get("binary_sensor.jev_daily_budget_exceeded").state == "on"
    # The answer keeps its last value and the usage entities keep explaining why,
    # rather than everything going blank at once.
    assert hass.states.get("sensor.jev_input_tokens_today").state == "321"
    assert hass.states.get("sensor.jev_calls_today").state == "1"


async def test_usage_survives_a_reload(hass, mock_client, config_entry):
    """A budget that a restart or an options change clears is not a budget."""
    await setup_with_context(hass, config_entry)
    assert hass.states.get("sensor.jev_input_tokens_today").state == "321"

    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    tokens = int(hass.states.get("sensor.jev_input_tokens_today").state)
    assert tokens >= 321, "the day's usage reset when the entry reloaded"


async def test_yesterdays_total_does_not_count_against_today(hass, mock_client, config_entry):
    stored = {"day": (date.today() - timedelta(days=1)).isoformat(),
              "calls": 99, "input_tokens": 999_999}
    with patch(
        "homeassistant.helpers.storage.Store.async_load", return_value=stored
    ):
        await setup_with_context(hass, config_entry)
    assert hass.states.get("sensor.jev_calls_today").state == "1"
