"""The AI Task entity as a decision provider, for `ai_task.evaluate`.

Home Assistant 2026.11 added decision tasks, with Jev's own question types. The
pinned test harness can be older, and there the action does not exist, so this module
skips rather than fails.
"""

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.ai_task import AITaskEntityFeature
from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from jevclient import (
    Choice,
    ChoiceAnswer,
    JevError,
    Noul,
    NoulAnswer,
    Score,
    ScoreAnswer,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jev.const import CONF_DAILY_TOKEN_BUDGET, DOMAIN

from .conftest import build_response

if not hasattr(AITaskEntityFeature, "EVALUATE"):
    pytest.skip(
        "decision tasks need Home Assistant 2026.11 or later",
        allow_module_level=True,
    )

ENTITY_ID = "ai_task.jev"

STATE = {"hall_window": "open", "outside_temperature": "4 °C"}

QUESTIONS = {
    "window_open": {"type": "noul", "instructions": "Is a window open?"},
    "room": {
        "type": "choice",
        "instructions": "Which room should be checked first?",
        "criteria": {
            "hall": "The hall",
            "attic": "The attic",
            "garage": "The garage",
        },
    },
    "urgency": {
        "type": "score",
        "instructions": "How urgent is closing it?",
        "criteria": ["Not urgent", "Soon", "Now"],
    },
}


@pytest.fixture
async def task_entry(hass, mock_client):
    """An entry of its own, so the budget here is not the shared fixture's."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Jev",
        data={CONF_API_KEY: "test-key-not-a-real-one"},
        options={},
        unique_id="ai-task-evaluate-entry",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def evaluate(hass, questions, state=STATE):
    return await hass.services.async_call(
        "ai_task",
        "evaluate",
        {
            "entity_id": ENTITY_ID,
            "task_name": "window check",
            "state": state,
            "questions": questions,
        },
        blocking=True,
        return_response=True,
    )


async def test_the_entity_offers_decision_tasks(hass, task_entry):
    """The flag is what puts the entity in the Decision default under Settings > AI."""
    features = hass.states.get(ENTITY_ID).attributes["supported_features"]
    assert features & AITaskEntityFeature.EVALUATE


async def test_each_question_goes_out_as_jev_takes_it(hass, mock_client, task_entry):
    mock_client.ask.return_value = build_response(
        window_open=NoulAnswer(noul=0.81),
        room=ChoiceAnswer(
            choice="hall", probabilities={"hall": 0.7, "attic": 0.3}, confidence=0.7
        ),
        urgency=ScoreAnswer(
            score=1.6,
            legend={"0": "Not urgent", "1": "Soon", "2": "Now"},
            probabilities={"1": 0.4, "2": 0.6},
            confidence=0.6,
        ),
    )

    await evaluate(hass, QUESTIONS)

    state, questions = mock_client.ask.await_args.args
    assert state == STATE
    assert questions == {
        "window_open": Noul(instructions="Is a window open?"),
        "room": Choice(
            instructions="Which room should be checked first?",
            criteria={
                "hall": "The hall",
                "attic": "The attic",
                "garage": "The garage",
            },
        ),
        "urgency": Score(
            instructions="How urgent is closing it?",
            criteria=["Not urgent", "Soon", "Now"],
        ),
    }


async def test_every_option_and_level_comes_back_with_a_probability(
    hass, mock_client, task_entry
):
    """Jev leaves out what it gives no weight. Home Assistant wants all of them."""
    mock_client.ask.return_value = build_response(
        window_open=NoulAnswer(noul=0.81),
        room=ChoiceAnswer(
            choice="hall", probabilities={"hall": 0.7, "attic": 0.3}, confidence=0.7
        ),
        urgency=ScoreAnswer(
            score=1.6,
            legend={"0": "Not urgent", "1": "Soon", "2": "Now"},
            probabilities={"1": 0.4, "2": 0.6},
            confidence=0.6,
        ),
    )

    result = await evaluate(hass, QUESTIONS)

    assert result == {
        "answers": {
            "window_open": {"type": "noul", "noul": 0.81},
            "room": {
                "type": "choice",
                "choice": "hall",
                "probabilities": {"hall": 0.7, "attic": 0.3, "garage": 0.0},
            },
            "urgency": {
                "type": "score",
                "score": 1.6,
                "probabilities": [0.0, 0.4, 0.6],
            },
        }
    }


async def test_a_text_state_is_sent_as_written(hass, mock_client, task_entry):
    mock_client.ask.return_value = build_response(window_open=NoulAnswer(noul=0.2))

    await evaluate(
        hass,
        {"window_open": QUESTIONS["window_open"]},
        state="the hall window reads closed",
    )

    state, _questions = mock_client.ask.await_args.args
    assert state == "the hall window reads closed"


async def test_a_score_with_more_levels_than_jev_takes_is_refused_unsent(
    hass, mock_client, task_entry
):
    calls_before = mock_client.ask.await_count
    with pytest.raises(ServiceValidationError) as err:
        await evaluate(
            hass,
            {
                "rating": {
                    "type": "score",
                    "instructions": "How bright is it?",
                    "criteria": [str(level) for level in range(11)],
                }
            },
        )
    assert err.value.translation_key == "question_levels_out_of_range"
    assert err.value.translation_placeholders == {
        "question": "rating",
        "count": "11",
        "min": "2",
        "max": "10",
    }
    assert mock_client.ask.await_count == calls_before


async def test_the_budget_refuses_the_task_before_it_is_sent(hass, mock_client):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Jev",
        data={CONF_API_KEY: "test-key-not-a-real-one"},
        options={CONF_DAILY_TOKEN_BUDGET: 50},
        unique_id="ai-task-evaluate-budget",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    calls_before = mock_client.ask.await_count

    with pytest.raises(HomeAssistantError) as err:
        await evaluate(hass, QUESTIONS)

    assert err.value.translation_key == "task_over_budget"
    assert mock_client.ask.await_count == calls_before


async def test_what_the_task_spends_lands_in_the_day_s_usage(
    hass, mock_client, task_entry
):
    mock_client.ask.return_value = build_response(window_open=NoulAnswer(noul=0.81))
    before = int(hass.states.get("sensor.jev_input_tokens_today").state)

    await evaluate(hass, {"window_open": QUESTIONS["window_open"]})
    await hass.async_block_till_done()

    assert int(hass.states.get("sensor.jev_input_tokens_today").state) == before + 321


async def test_an_answer_missing_from_the_response_is_an_error(
    hass, mock_client, task_entry
):
    """A missing answer is missing data. It is not reported as a probability of 0."""
    mock_client.ask.return_value = build_response(window_open=NoulAnswer(noul=0.81))

    with pytest.raises(HomeAssistantError) as err:
        await evaluate(hass, QUESTIONS)

    assert err.value.translation_key == "evaluation_answer_missing"
    assert err.value.translation_placeholders == {"question": "room", "type": "choice"}


async def test_a_refused_request_says_what_the_service_said(
    hass, mock_client, task_entry
):
    mock_client.ask = AsyncMock(side_effect=JevError("no answer from the endpoint"))
    with pytest.raises(HomeAssistantError, match="no answer from the endpoint"):
        await evaluate(hass, QUESTIONS)
