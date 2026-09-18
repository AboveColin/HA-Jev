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


async def test_the_state_is_built_from_the_named_entities(
    hass, mock_client, config_entry
):
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
            {
                "name": "Urgency",
                "type": "score",
                "instructions": "How urgent?",
                "criteria": levels,
            }
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


async def test_unloading_writes_the_totals_out_first(
    hass, mock_client, config_entry, hass_storage
):
    """The delayed write is a timer holding the only copy of the day's spend.

    `async_delay_save` arms a 15 second timer. Unload stopped the coordinator
    triggers and left that timer armed, so a reload or a shutdown inside the window
    dropped whatever had been recorded and the daily budget started the day over.
    """
    await setup_with_context(hass, config_entry)
    assert hass.states.get("sensor.jev_input_tokens_today").state == "321"

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    key = f"{DOMAIN}.{config_entry.entry_id}.usage"
    assert key in hass_storage, "unload left the day's usage in a pending timer"
    assert hass_storage[key]["data"]["input_tokens"] == 321


async def test_yesterdays_total_does_not_count_against_today(
    hass, mock_client, config_entry
):
    stored = {
        "day": (date.today() - timedelta(days=1)).isoformat(),
        "calls": 99,
        "input_tokens": 999_999,
    }
    with patch("homeassistant.helpers.storage.Store.async_load", return_value=stored):
        await setup_with_context(hass, config_entry)
    assert hass.states.get("sensor.jev_calls_today").state == "1"


async def test_a_yaml_question_takes_background_too(hass, mock_client, config_entry):
    context = {
        **CONTEXT,
        "questions": [
            {
                **CONTEXT["questions"][0],
                "background": "This machine draws under 5 W when idle.",
            }
        ],
    }
    await setup_with_context(hass, config_entry, context)
    sent = mock_client.ask.await_args.args[1]
    question = next(iter(sent.values()))
    assert (
        question.instructions["background"] == "This machine draws under 5 W when idle."
    )


async def test_an_unreachable_service_is_not_ready_rather_than_broken(
    hass, mock_client, config_entry
):
    """Setup must fail loudly, not leave a house full of entities that never fill."""
    from homeassistant.config_entries import ConfigEntryState
    from jevclient import JevConnectionError

    mock_client.ask.side_effect = JevConnectionError("no route to host")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_a_rejected_key_asks_for_a_new_one(hass, mock_client, config_entry):
    from homeassistant.config_entries import ConfigEntryState
    from jevclient import JevAuthError

    mock_client.ask.side_effect = JevAuthError("key revoked")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_an_outage_is_logged_once_and_recovery_once(
    hass, mock_client, config_entry, caplog
):
    """A 30 s context would otherwise write thousands of identical lines a day."""
    from jevclient import JevConnectionError

    await setup_with_context(hass, config_entry)
    coordinator = next(iter(config_entry.runtime_data.coordinators.values()))

    caplog.clear()
    mock_client.ask.side_effect = JevConnectionError("gone")
    for _ in range(3):
        await coordinator.async_refresh()
    assert sum("is not answering" in r.message for r in caplog.records) == 1

    caplog.clear()
    mock_client.ask.side_effect = None
    await coordinator.async_refresh()
    assert sum("answering again" in r.message for r in caplog.records) == 1


THREE_TYPES = {
    "name": "Everything",
    "entities": ["sensor.washer_power"],
    "questions": [
        {"name": "Forgotten", "type": "noul", "instructions": "Is it forgotten?"},
        {
            "name": "Room",
            "type": "choice",
            "instructions": "Which room?",
            "criteria": {"laundry": "Washer", "kitchen": None},
        },
        {
            "name": "Urgency",
            "type": "score",
            "instructions": "How urgent?",
            "criteria": ["No", "Soon", "Now"],
        },
    ],
}


async def test_each_question_type_becomes_the_right_kind_of_sensor(
    hass, mock_client, config_entry
):
    from jevclient import ChoiceAnswer, ScoreAnswer

    mock_client.ask.return_value = build_response(
        everything_forgotten=NoulAnswer(noul=0.42),
        everything_room=ChoiceAnswer(
            choice="laundry",
            probabilities={"laundry": 0.9, "kitchen": 0.1},
            confidence=0.88,
        ),
        everything_urgency=ScoreAnswer(
            score=1.25,
            legend={"0": "No", "1": "Soon", "2": "Now"},
            probabilities={"0": 0.1, "1": 0.55, "2": 0.35},
            confidence=0.6,
        ),
    )
    await setup_with_context(hass, config_entry, THREE_TYPES)

    noul = hass.states.get("sensor.jev_forgotten")
    assert noul.state == "0.42"
    # A noul carries no confidence: the probability is the whole answer.
    assert "confidence" not in noul.attributes

    choice = hass.states.get("sensor.jev_room")
    assert choice.state == "laundry"
    assert choice.attributes["confidence"] == 0.88
    assert choice.attributes["options"] == ["laundry", "kitchen"]

    score = hass.states.get("sensor.jev_urgency")
    assert score.state == "1.25"
    assert score.attributes["nearest_level"] == "Soon"
    assert score.attributes["legend"]["1"] == "Soon"

    # No threshold on any of them, so no binary sensor should have appeared.
    assert hass.states.get("binary_sensor.jev_forgotten") is None


async def test_a_broken_template_says_which_context_broke(
    hass, mock_client, config_entry
):
    context = {**CONTEXT, "state": "{{ 1 / 0 }}", "entities": None}
    context = {k: v for k, v in context.items() if v is not None}
    await setup_with_context(hass, config_entry, context)
    coordinator = next(iter(config_entry.runtime_data.coordinators.values()))
    assert not coordinator.last_update_success
    assert "Laundry" in str(coordinator.last_exception)


async def test_a_tracked_entity_changing_wakes_the_context(
    hass, mock_client, config_entry
):
    context = {**CONTEXT, "trigger_entities": ["sensor.washer_power"]}
    await setup_with_context(hass, config_entry, context)
    before = mock_client.ask.await_count

    hass.states.async_set("sensor.washer_power", "1450")
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()

    assert mock_client.ask.await_count > before


async def test_a_corrupt_usage_file_is_ignored_rather_than_trusted(
    hass, mock_client, config_entry
):
    """A stored day that will not parse must start the count at zero, not crash."""
    with patch(
        "homeassistant.helpers.storage.Store.async_load",
        return_value={"day": "not-a-date", "calls": 99, "input_tokens": 999},
    ):
        await setup_with_context(hass, config_entry)
    assert hass.states.get("sensor.jev_calls_today").state == "1"


async def test_a_latency_sensor_exists_per_context(hass, mock_client, config_entry):
    """Diagnostic and off by default, so it has to be read from the registry."""
    from homeassistant.helpers import entity_registry as er

    await setup_with_context(hass, config_entry)
    registry = er.async_get(hass)
    entry = registry.async_get("sensor.jev_laundry_latency")
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entry.entity_category is not None


@pytest.mark.parametrize(
    "question",
    [
        # A noul takes true/false descriptions, not criteria.
        {
            "name": "Q",
            "type": "noul",
            "instructions": "i",
            "criteria": {"a": "x", "b": "y"},
        },
        # true/false belong to a noul and nothing else.
        {
            "name": "Q",
            "type": "choice",
            "instructions": "i",
            "criteria": {"a": None, "b": None},
            "true": "yes",
        },
        # A threshold turns a noul into a binary sensor, so it means nothing elsewhere.
        {
            "name": "Q",
            "type": "score",
            "instructions": "i",
            "criteria": ["a", "b"],
            "threshold": 0.5,
        },
        # A choice needs its options as a mapping.
        {
            "name": "Q",
            "type": "choice",
            "instructions": "i",
            "criteria": ["a", "b"],
        },
    ],
)
async def test_yaml_refuses_a_question_shaped_wrong(hass, mock_client, question):
    assert not await async_setup_component(
        hass, DOMAIN, {DOMAIN: [{**CONTEXT, "questions": [question]}]}
    )


async def test_entities_accepts_the_full_picker_form(hass, mock_client, config_entry):
    """`entities:` takes a target mapping, not only a list of entity ids."""
    context = {**CONTEXT, "entities": {"entity_id": ["sensor.washer_power"]}}
    await setup_with_context(hass, config_entry, context)
    sent = mock_client.ask.await_args.args[0]
    assert sent["entities"][0]["entity_id"] == "sensor.washer_power"


async def test_the_day_rolls_over_at_midnight(hass, mock_client, config_entry):
    await setup_with_context(hass, config_entry)
    usage = config_entry.runtime_data.usage
    assert usage.calls == 1

    usage.roll_over(date.today() + timedelta(days=1))
    assert usage.calls == 0
    assert usage.input_tokens == 0
    assert usage.budget_exceeded is False


async def test_the_latency_sensor_reports_the_last_evaluation(
    hass, mock_client, config_entry
):
    from homeassistant.helpers import entity_registry as er

    await setup_with_context(hass, config_entry)
    registry = er.async_get(hass)
    registry.async_update_entity("sensor.jev_laundry_latency", disabled_by=None)
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.jev_laundry_latency").state == "274"


async def test_a_yaml_question_takes_structured_entries(hass, mock_client, config_entry):
    """instructions and every criteria value accept an object or an array.

    The actions already allowed this; the YAML schema did not, so the same question
    was legal through an action and rejected in configuration.yaml.
    """
    context = {
        **CONTEXT,
        "questions": [
            {
                "name": "Caller",
                "type": "choice",
                "instructions": {
                    "question": "What kind of caller is this?",
                    "focus": "What they are asking for, not how politely",
                },
                "criteria": {
                    "delivery": {
                        "what": "Dropping something off",
                        "not_for": "Anyone asking for money",
                        "examples": ["parcel for number 31"],
                    },
                    "sales": "Selling a contract at the door",
                    "other": None,
                },
            }
        ],
    }
    await setup_with_context(hass, config_entry, context)
    sent = next(iter(mock_client.ask.await_args.args[1].values()))
    assert sent.instructions["focus"] == "What they are asking for, not how politely"
    assert sent.criteria["delivery"]["examples"] == ["parcel for number 31"]
    assert sent.criteria["sales"] == "Selling a contract at the door"
    assert sent.criteria["other"] is None


async def test_a_yaml_score_takes_structured_levels(hass, mock_client, config_entry):
    context = {
        **CONTEXT,
        "questions": [
            {
                "name": "Severity",
                "type": "score",
                "instructions": "How severe is this?",
                "criteria": [
                    {"summary": "Cosmetic", "signals": ["wrong colour"]},
                    {
                        "summary": "Broken with a workaround",
                        "signals": ["needs a restart"],
                    },
                ],
            }
        ],
    }
    await setup_with_context(hass, config_entry, context)
    sent = next(iter(mock_client.ask.await_args.args[1].values()))
    assert sent.criteria[0]["summary"] == "Cosmetic"
