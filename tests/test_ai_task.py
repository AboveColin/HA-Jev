"""The AI Task entity: one question asked and answered inside a script.

A question subentry is a sensor on a schedule. This is the other shape, where the
script asks at the moment it runs and reads the answer in the next step.
"""

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from jevclient import ChoiceAnswer, JevAuthError, JevError, NoulAnswer, ScoreAnswer
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jev.const import CONF_DAILY_TOKEN_BUDGET, DOMAIN

from .conftest import build_response

ENTITY_ID = "ai_task.jev"

BOOLEAN_STRUCTURE = {
    "window_open": {
        "description": "Is a window open?",
        "selector": {"boolean": {}},
        "required": True,
    }
}


@pytest.fixture
async def task_entry(hass, mock_client):
    """An entry of its own, so the budget here is not the shared fixture's."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Jev",
        data={CONF_API_KEY: "test-key-not-a-real-one"},
        options={},
        unique_id="ai-task-entry",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def generate(hass, structure, instructions="the hall window reads open"):
    return await hass.services.async_call(
        "ai_task",
        "generate_data",
        {
            "entity_id": ENTITY_ID,
            "task_name": "window check",
            "instructions": instructions,
            "structure": structure,
        },
        blocking=True,
        return_response=True,
    )


async def test_the_entity_is_there_and_belongs_to_the_service_device(hass, task_entry):
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes["supported_features"] == 1  # GENERATE_DATA, nothing else

    entity_entry = er.async_get(hass).async_get(ENTITY_ID)
    assert entity_entry.unique_id == f"{task_entry.entry_id}_ai_task"
    device = dr.async_get(hass).async_get(entity_entry.device_id)
    assert device.identifiers == {(DOMAIN, task_entry.entry_id)}


async def test_a_boolean_field_comes_back_as_a_boolean(hass, mock_client, task_entry):
    mock_client.ask.return_value = build_response(window_open=NoulAnswer(noul=0.81))

    result = await generate(hass, BOOLEAN_STRUCTURE)

    assert result["data"]["window_open"] is True
    state, questions = mock_client.ask.await_args.args
    assert state == "the hall window reads open"
    assert questions["window_open"].instructions == "Is a window open?"


async def test_the_confidence_travels_beside_the_values(hass, mock_client, task_entry):
    """A bare `true` is the one thing a caller cannot act on carefully."""
    mock_client.ask.return_value = build_response(
        window_open=NoulAnswer(noul=0.81),
        room=ChoiceAnswer(
            choice="hall", probabilities={"hall": 0.7, "attic": 0.3}, confidence=0.7
        ),
        urgency=ScoreAnswer(
            score=3.0,
            legend={"0": "1", "1": "2", "2": "3", "3": "4", "4": "5"},
            probabilities={"3": 0.8},
            confidence=0.8,
        ),
    )

    result = await generate(
        hass,
        BOOLEAN_STRUCTURE
        | {
            "room": {
                "description": "Which room?",
                "selector": {"select": {"options": ["hall", "attic"]}},
            },
            "urgency": {
                "description": "How urgent?",
                "selector": {"number": {"min": 1, "max": 5, "step": 1}},
            },
        },
    )

    assert result["data"]["room"] == "hall"
    assert result["data"]["urgency"] == 4
    detail = result["data"]["jev"]
    assert detail["input_tokens"] == 321
    assert detail["answers"]["window_open"] == {"probability": 0.81}
    assert detail["answers"]["room"]["confidence"] == 0.7
    assert detail["answers"]["urgency"]["nearest_level"] == "4"


async def test_a_task_with_no_structure_is_refused_without_a_request(
    hass, mock_client, task_entry
):
    """Jev answers typed questions. Free text would have to be invented."""
    calls_before = mock_client.ask.await_count
    with pytest.raises(ServiceValidationError, match="free text"):
        await hass.services.async_call(
            "ai_task",
            "generate_data",
            {
                "entity_id": ENTITY_ID,
                "task_name": "window check",
                "instructions": "is the window open?",
            },
            blocking=True,
            return_response=True,
        )
    assert mock_client.ask.await_count == calls_before


async def test_the_budget_refuses_the_task_before_it_is_sent(hass, mock_client):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Jev",
        data={CONF_API_KEY: "test-key-not-a-real-one"},
        options={CONF_DAILY_TOKEN_BUDGET: 50},
        unique_id="ai-task-budget",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    calls_before = mock_client.ask.await_count

    with pytest.raises(HomeAssistantError, match="daily budget"):
        await generate(hass, BOOLEAN_STRUCTURE)

    assert mock_client.ask.await_count == calls_before


async def test_a_refused_request_says_what_the_service_said(
    hass, mock_client, task_entry
):
    mock_client.ask = AsyncMock(side_effect=JevError("no answer from the endpoint"))
    with pytest.raises(HomeAssistantError, match="no answer from the endpoint"):
        await generate(hass, BOOLEAN_STRUCTURE)


async def test_a_rejected_key_asks_for_a_new_one(hass, mock_client, task_entry):
    mock_client.ask = AsyncMock(side_effect=JevAuthError("revoked"))
    with pytest.raises(HomeAssistantError) as err:
        await generate(hass, BOOLEAN_STRUCTURE)
    assert err.value.translation_key == "auth_rejected"
    await hass.async_block_till_done()
    [flow] = task_entry.async_get_active_flows(hass, {SOURCE_REAUTH})
    assert flow["step_id"] == "reauth_confirm"


async def test_what_the_task_spends_lands_in_the_day_s_usage(
    hass, mock_client, task_entry
):
    """A task and a context spend from the same budget, because they share a key."""
    mock_client.ask.return_value = build_response(window_open=NoulAnswer(noul=0.81))
    before = int(hass.states.get("sensor.jev_input_tokens_today").state)

    await generate(hass, BOOLEAN_STRUCTURE)
    await hass.async_block_till_done()

    assert int(hass.states.get("sensor.jev_input_tokens_today").state) == before + 321
    usage = task_entry.runtime_data.usage
    assert usage.day == dt_util.now().date()


async def test_a_task_in_flight_holds_its_estimate_against_the_budget(
    hass, mock_client, task_entry
):
    """A context that checks the budget while a task waits must see the task."""
    usage = task_entry.runtime_data.usage
    held = []

    async def answer(*_args, **_kwargs):
        held.append(usage.reserved)
        return build_response(window_open=NoulAnswer(noul=0.81))

    mock_client.ask.side_effect = answer
    await generate(hass, BOOLEAN_STRUCTURE)

    assert held and held[0] > 0
    assert usage.reserved == 0
