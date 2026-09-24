"""The four actions, including what they refuse."""

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from jevclient import ChoiceAnswer, NoulAnswer, ScoreAnswer
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jev.const import DOMAIN
from custom_components.jev.payload import payload_bytes

from .conftest import build_response


async def call(hass, action, data):
    return await hass.services.async_call(
        DOMAIN, action, data, blocking=True, return_response=True
    )


async def test_noul_returns_the_probability_and_the_threshold(
    hass, loaded_entry, mock_client
):
    mock_client.ask.return_value = build_response(answer=NoulAnswer(noul=0.81))
    response = await call(
        hass,
        "noul",
        {
            "state": "The machine has drawn 1.2 W for eight minutes.",
            "instructions": "Is the programme finished?",
            "threshold": 0.7,
        },
    )
    assert response["noul"] == 0.81
    assert response["is_true"] is True
    assert response["threshold"] == 0.7
    assert response["usage"]["input_tokens"] == 321
    assert response["model"] == "jev-1.13.0"


async def test_an_action_teaches_the_budget_how_big_a_token_is(
    hass, loaded_entry, mock_client
):
    """The budget estimate divides request bytes by this ratio, so every call counts."""
    mock_client.ask.return_value = build_response(answer=NoulAnswer(noul=0.81))
    await call(
        hass,
        "noul",
        {"state": "The machine has drawn 1.2 W.", "instructions": "Is it done?"},
    )
    state, questions = mock_client.ask.call_args.args
    runtime = loaded_entry.runtime_data
    sent = payload_bytes(state, questions, runtime.model)
    assert runtime.usage.bytes_per_token == sent / 321


async def test_the_threshold_is_the_callers_and_nothing_else(
    hass, loaded_entry, mock_client
):
    """0.55 is a yes at 0.5 and a no at 0.7. The probability never changes."""
    mock_client.ask.return_value = build_response(answer=NoulAnswer(noul=0.55))
    low = await call(hass, "noul", {"state": "x", "instructions": "y", "threshold": 0.5})
    high = await call(hass, "noul", {"state": "x", "instructions": "y", "threshold": 0.7})
    assert low["noul"] == high["noul"] == 0.55
    assert low["is_true"] is True
    assert high["is_true"] is False


async def test_choice_sends_the_options_and_returns_the_distribution(
    hass, loaded_entry, mock_client
):
    mock_client.ask.return_value = build_response(
        answer=ChoiceAnswer(
            choice="auth",
            probabilities={"auth": 0.96, "storage": 0.04},
            confidence=0.95,
        )
    )
    response = await call(
        hass,
        "choice",
        {
            "state": "400 Invalid Customer",
            "instructions": "Which area owns this?",
            "options": ["auth", "storage"],
            "option_descriptions": {"auth": "Tokens and credentials"},
        },
    )
    assert response["choice"] == "auth"
    assert response["probabilities"] == {"auth": 0.96, "storage": 0.04}

    sent = mock_client.ask.await_args.args[1]["answer"]
    # A described option keeps its description; an undescribed one goes as null,
    # which the API accepts and reads as "the name says enough".
    assert sent.criteria == {"auth": "Tokens and credentials", "storage": None}


async def test_score_normalizes_against_its_own_top_level(
    hass, loaded_entry, mock_client
):
    mock_client.ask.return_value = build_response(
        answer=ScoreAnswer(
            score=2.1,
            legend={"0": "Ignore", "1": "This week", "2": "Today", "3": "Wake someone"},
            probabilities={"0": 0.0, "1": 0.1, "2": 0.7, "3": 0.2},
            confidence=0.84,
        )
    )
    response = await call(
        hass,
        "score",
        {
            "state": "the unit failed twice",
            "instructions": "How urgent?",
            "levels": ["Ignore", "This week", "Today", "Wake someone"],
        },
    )
    assert response["score"] == 2.1
    assert response["normalized"] == pytest.approx(0.7)
    assert response["nearest_level"] == "Today"


async def test_ask_returns_every_answer_under_the_callers_own_keys(
    hass, loaded_entry, mock_client
):
    mock_client.ask.return_value = build_response(
        real=NoulAnswer(noul=0.77),
        urgency=ScoreAnswer(
            score=1.4,
            legend={"0": "No", "1": "Today"},
            probabilities={"0": 0.3, "1": 0.7},
            confidence=0.6,
        ),
    )
    response = await call(
        hass,
        "ask",
        {
            "state": "something happened",
            "questions": {
                "real": {"type": "noul", "instructions": "Is it real?"},
                "urgency": {
                    "type": "score",
                    "instructions": "How urgent?",
                    "criteria": ["No", "Today"],
                },
            },
        },
    )
    assert set(response["answers"]) == {"real", "urgency"}
    assert response["answers"]["real"]["noul"] == 0.77
    assert response["answers"]["urgency"]["nearest_level"] == "Today"


@pytest.mark.parametrize(
    ("action", "data", "fragment"),
    [
        (
            "score",
            {"state": "x", "instructions": "y", "levels": ["one"]},
            "2 to 10 levels",
        ),
        (
            "score",
            {"state": "x", "instructions": "y", "levels": [str(i) for i in range(11)]},
            "2 to 10 levels",
        ),
        (
            "choice",
            {"state": "x", "instructions": "y", "options": ["one"]},
            "2 to 255 options",
        ),
        (
            "ask",
            {"state": "x", "questions": {"q": {"type": "noul"}}},
            "needs both 'type' and 'instructions'",
        ),
        ("noul", {"instructions": "y"}, "give this action some text"),
    ],
)
async def test_bad_input_is_refused_before_a_request_is_spent(
    hass, loaded_entry, mock_client, action, data, fragment
):
    """Every message names the limit and what was given, and costs nothing."""
    mock_client.ask.reset_mock()
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, action, data)
    # str() resolves through Home Assistant's translation system, so this also
    # proves the strings file renders with the placeholders filled in.
    assert fragment in str(err.value)
    assert mock_client.ask.await_count == 0


async def test_a_refusal_names_the_number_that_was_actually_given(
    hass, loaded_entry, mock_client
):
    """A message saying "2 to 10" without saying you passed 11 is half a message."""
    with pytest.raises(ServiceValidationError) as err:
        await call(
            hass,
            "score",
            {
                "state": "x",
                "instructions": "y",
                "levels": [str(i) for i in range(11)],
            },
        )
    message = str(err.value)
    assert "11" in message and "2 to 10" in message


async def test_an_unrendered_template_is_rendered(hass, loaded_entry, mock_client):
    """A call from the developer tools or REST arrives with the template intact."""
    hass.states.async_set("sensor.watts", "1.2")
    await call(
        hass,
        "noul",
        {
            "state": "Power is {{ states('sensor.watts') }} W",
            "instructions": "Is it idle?",
        },
    )
    assert mock_client.ask.await_args.args[0] == "Power is 1.2 W"


async def test_without_background_the_question_stays_a_plain_string(
    hass, loaded_entry, mock_client
):
    await call(hass, "noul", {"state": "x", "instructions": "Is it idle?"})
    assert mock_client.ask.await_args.args[1]["answer"].instructions == "Is it idle?"


async def test_background_travels_with_the_question_not_the_state(
    hass, loaded_entry, mock_client
):
    """Standing facts belong to the question. Measured: readings alone separated two
    situations by 0.21, the same rule written into the question by 0.60."""
    await call(
        hass,
        "noul",
        {
            "state": "Power: 1.2 W",
            "instructions": "Is it idle?",
            "background": "This machine draws under 5 W when idle.",
        },
    )
    state, questions = mock_client.ask.await_args.args
    assert questions["answer"].instructions == {
        "question": "Is it idle?",
        "background": "This machine draws under 5 W when idle.",
    }
    # and it did not end up in the state, where it measured worse
    assert state == "Power: 1.2 W"


async def test_a_mapping_background_keeps_the_authors_own_key_names(
    hass, loaded_entry, mock_client
):
    """The model reads the key, and only the author knows what to call it."""
    mock_client.ask.return_value = build_response(
        answer=ScoreAnswer(
            score=1.0,
            legend={"0": "No", "1": "Yes"},
            probabilities={"0": 0.0, "1": 1.0},
            confidence=1.0,
        )
    )
    await call(
        hass,
        "score",
        {
            "state": "x",
            "instructions": "How urgent?",
            "levels": ["No", "Yes"],
            "background": {"how_to_read_the_power": "Under 5 W means idle."},
        },
    )
    assert mock_client.ask.await_args.args[1]["answer"].instructions == {
        "question": "How urgent?",
        "how_to_read_the_power": "Under 5 W means idle.",
    }


async def test_an_answer_of_the_wrong_type_says_so(hass, loaded_entry, mock_client):
    """Schema-guaranteed output is still somebody else's guarantee."""
    mock_client.ask.return_value = build_response(answer=NoulAnswer(noul=0.5))
    with pytest.raises(HomeAssistantError, match="which the API should not do"):
        await call(
            hass,
            "choice",
            {
                "state": "x",
                "instructions": "y",
                "options": ["a", "b"],
            },
        )


async def test_a_structured_state_is_passed_through_untouched(
    hass, loaded_entry, mock_client
):
    """An automation variable holding a mapping must stay a mapping.

    Home Assistant renders action data natively, so `{{ machine }}` arrives as a
    dict with real ints. Flattening it here would throw away the field names the
    model reads as labels.
    """
    state = {
        "trigger_value": "a ZEBRA walked past",
        "room": "laundry",
        "machine": {"brand": "Miele", "idle_watts": 5, "running_watts": 300},
    }
    await call(
        hass, "noul", {"state": state, "instructions": "Is `machine.idle_watts` 5?"}
    )
    sent = mock_client.ask.await_args.args[0]
    assert sent == state
    assert isinstance(sent["machine"]["idle_watts"], int)


async def test_a_list_state_survives_too(hass, loaded_entry, mock_client):
    """Arrays suit a sequence of messages or records, per the API docs."""
    state = [
        {"from": "a housemate", "text": "is the washing done"},
        {"from": "sensor", "text": "1.2 W"},
    ]
    await call(
        hass, "noul", {"state": state, "instructions": "Is anyone asking a question?"}
    )
    assert mock_client.ask.await_args.args[0] == state


async def test_a_template_inside_a_structured_state_is_left_alone(
    hass, loaded_entry, mock_client
):
    """Only a bare unrendered string is rendered here.

    Inside a mapping the script engine has already done it, and re-rendering values
    we did not render would be guessing at somebody else's data.
    """
    hass.states.async_set("sensor.watts", "1.2")
    state = {"reading": "{{ states('sensor.watts') }}"}
    await call(hass, "noul", {"state": state, "instructions": "Is it idle?"})
    assert mock_client.ask.await_args.args[0] == state


async def test_a_rejected_key_during_an_action_says_so(hass, loaded_entry, mock_client):
    from jevclient import JevAuthError

    mock_client.ask.side_effect = JevAuthError("revoked")
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "noul", {"state": "x", "instructions": "y"})
    assert err.value.translation_key == "auth_rejected"
    # Setup and the coordinators asked for a new key, and an action did not.
    await hass.async_block_till_done()
    [flow] = loaded_entry.async_get_active_flows(hass, {SOURCE_REAUTH})
    assert flow["step_id"] == "reauth_confirm"


async def test_a_transport_failure_during_an_action_says_so(
    hass, loaded_entry, mock_client
):
    from jevclient import JevConnectionError

    mock_client.ask.side_effect = JevConnectionError("no route")
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "noul", {"state": "x", "instructions": "y"})
    assert err.value.translation_key == "ask_failed"


async def test_asking_with_a_named_entry_picks_that_entry(
    hass, loaded_entry, mock_client
):
    response = await call(
        hass,
        "noul",
        {
            "state": "x",
            "instructions": "y",
            "config_entry": loaded_entry.entry_id,
        },
    )
    assert "noul" in response


async def test_ask_with_no_questions_is_refused_before_a_request_is_spent(
    hass, loaded_entry, mock_client
):
    """An empty mapping was sent, billed, and answered with nothing."""
    mock_client.ask.reset_mock()
    with pytest.raises(vol.Invalid, match="length of value must be at least 1"):
        await call(hass, "ask", {"state": "x", "questions": {}})
    assert mock_client.ask.await_count == 0


async def test_a_target_that_names_only_absent_entities_is_refused(
    hass, loaded_entry, mock_client
):
    """An entity id that exists nowhere is referenced, not missing, so it passed."""
    mock_client.ask.reset_mock()
    with pytest.raises(ServiceValidationError) as err:
        await call(
            hass,
            "noul",
            {"entity_id": ["sensor.not_there"], "instructions": "Is it on?"},
        )
    assert err.value.translation_key == "empty_target"
    assert mock_client.ask.await_count == 0


async def test_an_action_with_two_entries_and_none_named_is_refused(
    hass, loaded_entry, mock_client
):
    """Each entry has its own key and budget, and the first one loaded paid."""
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Jev guest",
        data={CONF_API_KEY: "another-key-not-a-real-one"},
        unique_id="fedcba9876543210",
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    mock_client.ask.reset_mock()

    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "noul", {"state": "x", "instructions": "y"})
    assert err.value.translation_key == "entry_ambiguous"
    assert err.value.translation_placeholders == {"entries": "Jev, Jev guest"}
    assert mock_client.ask.await_count == 0

    named = await call(
        hass,
        "noul",
        {"state": "x", "instructions": "y", "config_entry": second.entry_id},
    )
    assert "noul" in named


async def test_without_the_recorder_it_says_so(hass, loaded_entry):
    with pytest.raises(ServiceValidationError) as err:
        await call(
            hass,
            "calibrate",
            {"entity_id": "sensor.x", "truth_entity_id": "binary_sensor.y"},
        )
    assert err.value.translation_key == "calibrate_needs_recorder"


async def test_an_action_that_would_pass_the_budget_is_not_sent(
    hass, loaded_entry, mock_client
):
    usage = loaded_entry.runtime_data.usage
    usage.budget = 1000
    usage.input_tokens = 999
    mock_client.ask.reset_mock()

    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "noul", {"state": "x", "instructions": "Is it done?"})

    assert mock_client.ask.await_count == 0
    assert err.value.translation_key == "action_over_budget"
    placeholders = err.value.translation_placeholders
    assert placeholders["remaining"] == "1"
    assert placeholders["budget"] == "1000"
    assert int(placeholders["estimate"]) > 1


async def test_an_action_holds_its_estimate_while_it_runs(
    hass, loaded_entry, mock_client
):
    usage = loaded_entry.runtime_data.usage
    held = []

    async def watch(state, questions):
        held.append(usage.reserved)
        return build_response(answer=NoulAnswer(noul=0.5))

    mock_client.ask.side_effect = watch
    await call(hass, "noul", {"state": "x", "instructions": "Is it done?"})

    assert held[0] > 0
    assert usage.reserved == 0
