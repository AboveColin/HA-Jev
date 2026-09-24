"""The conversation agent: what it acts on, and what it refuses to act on.

Every test drives the real `conversation.async_converse` entry point and then
asserts on entity state, so what is checked is the effect on the house rather than
which helper got called.
"""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import Context, ServiceCall
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import chat_session
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent as ha_intent
from homeassistant.helpers.chat_session import CONVERSATION_TIMEOUT
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from jevclient import ChoiceAnswer, NoulAnswer, Usage

from custom_components.jev.const import (
    CONF_ALLOW_WHOLE_HOME,
    CONF_FALLBACK_AGENT,
    CONF_MIN_CONFIDENCE,
    CONVERSATION_TRACE_LENGTH,
)
from custom_components.jev.conversation import _render_state_answer
from custom_components.jev.interpret import NONE, find_brightness
from custom_components.jev.payload import payload_bytes

from .conftest import PROBE_TOKENS, build_response

AGENT = "conversation.jev"


def answer_set(**overrides):
    """A confident, single, device-level turn_on, with each field overridable."""
    base = {
        "action": ChoiceAnswer(choice="turn_on", probabilities={}, confidence=0.97),
        "compound": NoulAnswer(noul=0.02),
        "free_text": NoulAnswer(noul=0.01),
        "target_type": ChoiceAnswer(choice="entity", probabilities={}, confidence=0.9),
        "entity": ChoiceAnswer(choice="light.kitchen", probabilities={}, confidence=1.0),
        "area": ChoiceAnswer(choice="none_of_these", probabilities={}, confidence=0.4),
        "domain": ChoiceAnswer(choice="light", probabilities={}, confidence=0.95),
    }
    base.update(overrides)
    return base


@pytest.fixture
async def house(hass, mock_client, config_entry):
    """Two exposed lights in two rooms, plus one that is deliberately not exposed."""
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    assert await async_setup_component(hass, "light", {})

    areas = ar.async_get(hass)
    kitchen = areas.async_get_or_create("Kitchen")
    office = areas.async_get_or_create("Office")

    entities = er.async_get(hass)
    for entity_id, name, area in (
        ("light.kitchen", "Kitchen light", kitchen),
        ("light.office", "Office light", office),
        ("light.private", "Private light", office),
    ):
        domain, object_id = entity_id.split(".")
        entry = entities.async_get_or_create(
            domain, "demo", object_id, suggested_object_id=object_id
        )
        entities.async_update_entity(entry.entity_id, name=name, area_id=area.id)
        hass.states.async_set(entity_id, "off", {"friendly_name": name})

    async_expose_entity(hass, conversation.DOMAIN, "light.kitchen", True)
    async_expose_entity(hass, conversation.DOMAIN, "light.office", True)
    async_expose_entity(hass, conversation.DOMAIN, "light.private", False)

    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # The real API answers only what it was asked. A mock that also answered the
    # domain question on every turn hid a command that never asked it.
    async def asked_only(state, questions):
        full = mock_client.ask.return_value
        kept = {k: v for k, v in full.answers.items() if k in questions}
        return replace(full, answers=kept)

    mock_client.ask.side_effect = asked_only
    return config_entry


async def converse(hass, text, agent_id=AGENT, language="en"):
    return await conversation.async_converse(
        hass, text, None, Context(), language=language, agent_id=agent_id
    )


async def test_the_agent_registers_as_an_entity(hass, house):
    assert hass.states.get(AGENT) is not None


async def test_a_named_device_is_turned_on(hass, house, mock_client):
    mock_client.ask.return_value = build_response(**answer_set())
    calls = []

    async def record(call):
        calls.append(call)

    hass.services.async_register("light", "turn_on", record)

    result = await converse(hass, "could you put the kitchen light on")
    await hass.async_block_till_done()

    assert result.response.response_type is not None
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == ["light.kitchen"]


async def test_only_exposed_entities_are_ever_sent(hass, house, mock_client):
    mock_client.ask.return_value = build_response(**answer_set())
    await converse(hass, "kitchen light on")

    state = mock_client.ask.call_args.args[0]
    sent = {e["entity_id"] for e in state["entities"]}
    assert sent == {"light.kitchen", "light.office"}
    assert "light.private" not in str(state)


async def test_the_command_travels_with_the_state(hass, house, mock_client):
    mock_client.ask.return_value = build_response(**answer_set())
    await converse(hass, "kitchen light on")
    assert mock_client.ask.call_args.args[0]["command"] == "kitchen light on"


async def test_every_question_goes_in_one_request(hass, house, mock_client):
    mock_client.ask.return_value = build_response(**answer_set())
    # Setup already made the probe call, so only what the sentence costs is counted.
    mock_client.ask.reset_mock()
    await converse(hass, "kitchen light on")

    assert mock_client.ask.await_count == 1
    questions = mock_client.ask.call_args.args[1]
    assert {"action", "compound", "free_text", "target_type", "entity", "area"} <= set(
        questions
    )


async def test_an_area_command_reaches_both_lights_in_that_area(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        **answer_set(
            target_type=ChoiceAnswer(choice="area", probabilities={}, confidence=0.94),
            entity=ChoiceAnswer(choice="none_of_these", probabilities={}, confidence=0.9),
            area=ChoiceAnswer(choice="Office", probabilities={}, confidence=0.93),
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    await converse(hass, "lights on in the office")
    await hass.async_block_till_done()

    # light.private is in the Office too, and is not exposed, so it stays out.
    assert [c.data["entity_id"] for c in calls] == [["light.office"]]


async def test_a_confident_device_beats_a_vague_area(hass, house, mock_client):
    """The measured case: scope at 0.41 alongside a device at 1.00.

    Branching on scope first turned on every light in the house. The device answer
    is the certain one and has to win.
    """
    mock_client.ask.return_value = build_response(
        **answer_set(
            target_type=ChoiceAnswer(choice="area", probabilities={}, confidence=0.41),
            area=ChoiceAnswer(choice="Office", probabilities={}, confidence=0.41),
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    await converse(hass, "turn on the kitchen light")
    await hass.async_block_till_done()

    assert [c.data["entity_id"] for c in calls] == [["light.kitchen"]]


async def test_a_compound_command_acts_on_nothing(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        **answer_set(compound=NoulAnswer(noul=0.94))
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    result = await converse(hass, "kitchen light on and lock the door")
    await hass.async_block_till_done()

    assert calls == []
    assert "did not understand" in result.response.speech["plain"]["speech"]
    assert result.response.error_code is ha_intent.IntentResponseErrorCode.NO_INTENT_MATCH


async def test_low_confidence_acts_on_nothing(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="turn_on", probabilities={}, confidence=0.31)
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    await converse(hass, "mmh the thing")
    await hass.async_block_till_done()
    assert calls == []


async def test_the_confidence_floor_is_configurable(hass, house, mock_client):
    hass.config_entries.async_update_entry(house, options={CONF_MIN_CONFIDENCE: 0.99})
    await hass.async_block_till_done()
    mock_client.ask.return_value = build_response(**answer_set())
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    await converse(hass, "kitchen light on")
    await hass.async_block_till_done()
    # The action answer is 0.97, under the floor the user asked for.
    assert calls == []


async def test_whole_house_turn_on_is_refused(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        **answer_set(
            target_type=ChoiceAnswer(
                choice="everything", probabilities={}, confidence=0.9
            ),
            entity=ChoiceAnswer(choice="none_of_these", probabilities={}, confidence=0.9),
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    result = await converse(hass, "turn everything on")
    await hass.async_block_till_done()

    assert calls == []
    assert "whole house" in result.response.speech["plain"]["speech"]


async def test_whole_house_turn_off_is_allowed(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="turn_off", probabilities={}, confidence=0.95),
            target_type=ChoiceAnswer(
                choice="everything", probabilities={}, confidence=0.9
            ),
            entity=ChoiceAnswer(choice="none_of_these", probabilities={}, confidence=0.9),
        )
    )
    calls = []
    hass.services.async_register("light", "turn_off", lambda call: calls.append(call))

    await converse(hass, "turn everything off")
    await hass.async_block_till_done()
    assert len(calls) >= 1


async def test_whole_house_can_be_allowed(hass, house, mock_client):
    hass.config_entries.async_update_entry(house, options={CONF_ALLOW_WHOLE_HOME: True})
    await hass.async_block_till_done()
    mock_client.ask.return_value = build_response(
        **answer_set(
            target_type=ChoiceAnswer(
                choice="everything", probabilities={}, confidence=0.9
            ),
            entity=ChoiceAnswer(choice="none_of_these", probabilities={}, confidence=0.9),
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    await converse(hass, "turn everything on")
    await hass.async_block_till_done()
    assert len(calls) >= 1


async def test_a_free_text_request_goes_to_the_fallback_agent(hass, house, mock_client):
    hass.config_entries.async_update_entry(
        house, options={CONF_FALLBACK_AGENT: "conversation.home_assistant"}
    )
    await hass.async_block_till_done()
    mock_client.ask.return_value = build_response(
        **answer_set(free_text=NoulAnswer(noul=0.88))
    )

    with patch(
        "custom_components.jev.conversation.conversation.async_converse",
        wraps=conversation.async_converse,
    ) as handed_over:
        await converse(hass, "add milk to the shopping list")

    handovers = [
        c for c in handed_over.await_args_list if c.kwargs.get("agent_id") != AGENT
    ]
    assert len(handovers) == 1
    assert handovers[0].args[1] == "add milk to the shopping list"
    assert handovers[0].kwargs["agent_id"] == "conversation.home_assistant"


async def test_the_fallback_never_points_at_itself(hass, house, mock_client):
    hass.config_entries.async_update_entry(house, options={CONF_FALLBACK_AGENT: AGENT})
    await hass.async_block_till_done()
    mock_client.ask.return_value = build_response(
        **answer_set(compound=NoulAnswer(noul=0.99))
    )

    result = await converse(hass, "do two things")
    assert "did not understand" in result.response.speech["plain"]["speech"]


async def test_a_voice_command_counts_against_the_budget(hass, house, mock_client):
    mock_client.ask.return_value = build_response(**answer_set())
    await converse(hass, "kitchen light on")
    await hass.async_block_till_done()

    # Setup's probe is the other call.
    assert hass.states.get("sensor.jev_calls_today").state == "2"
    assert hass.states.get("sensor.jev_input_tokens_today").state == str(
        321 + PROBE_TOKENS
    )


async def test_a_spent_budget_stops_voice_too(hass, house, mock_client):
    hass.config_entries.async_update_entry(house, options={"daily_token_budget": 10})
    await hass.async_block_till_done()
    house.runtime_data.usage.input_tokens = 999
    mock_client.ask.reset_mock()

    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))
    result = await converse(hass, "kitchen light on")
    await hass.async_block_till_done()

    assert mock_client.ask.await_count == 0
    assert calls == []
    assert "budget is left" in result.response.speech["plain"]["speech"]
    assert result.response.response_type is ha_intent.IntentResponseType.ERROR


async def test_a_command_that_would_pass_the_budget_is_not_sent(hass, house, mock_client):
    # One token short of the budget. would_exceed() says there is room, and on
    # 1.15 the command went through and ended the day about 1,000 tokens over.
    hass.config_entries.async_update_entry(house, options={"daily_token_budget": 1000})
    await hass.async_block_till_done()
    house.runtime_data.usage.input_tokens = 999
    mock_client.ask.reset_mock()

    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))
    result = await converse(hass, "kitchen light on")
    await hass.async_block_till_done()

    assert mock_client.ask.await_count == 0
    assert calls == []
    assert "budget is left" in result.response.speech["plain"]["speech"]
    assert result.response.response_type is ha_intent.IntentResponseType.ERROR


async def test_a_voice_command_teaches_the_estimate(hass, house, mock_client):
    # The estimate reads bytes per token from the last call it could measure. A
    # voice command that did not report its size left the estimate on whatever
    # the last context taught it, which is a different shape of request.
    mock_client.ask.return_value = replace(
        build_response(**answer_set()), usage=Usage(input_tokens=1371, output_tokens=42)
    )
    usage = house.runtime_data.usage

    with patch.object(usage, "record", wraps=usage.record) as record:
        await converse(hass, "kitchen light on")
        await hass.async_block_till_done()

    sent_state, sent_questions = mock_client.ask.await_args.args
    sent = payload_bytes(sent_state, sent_questions, house.runtime_data.model)
    record.assert_called_once_with(1371, sent)


async def test_a_rejected_key_is_said_out_loud(hass, house, mock_client):
    from jevclient import JevAuthError

    mock_client.ask.side_effect = JevAuthError("bad key")
    result = await converse(hass, "kitchen light on")
    assert "rejected the API key" in result.response.speech["plain"]["speech"]
    await hass.async_block_till_done()
    [flow] = house.async_get_active_flows(hass, {SOURCE_REAUTH})
    assert flow["step_id"] == "reauth_confirm"


async def test_the_fallback_never_points_at_another_jev_agent(hass, house, mock_client):
    """Two Jev agents that fall back to each other would pay for every pass."""
    other = er.async_get(hass).async_get_or_create(
        "conversation", "jev", "other", suggested_object_id="jev_other"
    )
    hass.config_entries.async_update_entry(
        house, options={CONF_FALLBACK_AGENT: other.entity_id}
    )
    await hass.async_block_till_done()
    mock_client.ask.return_value = build_response(
        **answer_set(compound=NoulAnswer(noul=0.99))
    )

    with patch(
        "custom_components.jev.conversation.conversation.async_converse",
        wraps=conversation.async_converse,
    ) as handed_over:
        result = await converse(hass, "do two things")

    assert [c for c in handed_over.await_args_list if c.kwargs["agent_id"] != AGENT] == []
    assert "did not understand" in result.response.speech["plain"]["speech"]


async def test_an_api_failure_acts_on_nothing(hass, house, mock_client):
    from jevclient import JevError

    mock_client.ask.side_effect = JevError("upstream is down")
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    result = await converse(hass, "kitchen light on")
    await hass.async_block_till_done()

    assert calls == []
    assert "did not answer" in result.response.speech["plain"]["speech"]


async def test_a_device_that_is_not_exposed_is_never_acted_on(hass, house, mock_client):
    """The model can only name what it was shown, but the guard is checked anyway."""
    mock_client.ask.return_value = build_response(
        **answer_set(
            entity=ChoiceAnswer(choice="light.private", probabilities={}, confidence=1.0)
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    await converse(hass, "private light on")
    await hass.async_block_till_done()
    assert calls == []


@pytest.mark.parametrize(
    ("text", "acts"),
    [
        # The hidden name is said, in more words than the one the model picked.
        ("turn on the kitchen light strip", False),
        ("turn on the private light", False),
        # Only the exposed name is said, or the hidden one is not said whole.
        ("turn on the kitchen light", True),
        ("turn on the kitchen lights", True),
    ],
)
async def test_a_hidden_device_named_in_full_is_not_swapped_for_an_exposed_one(
    hass, house, mock_client, text, acts
):
    """Measured: an unexposed "Desk lamp" was said, and the exposed "Lamp" went on."""
    hass.states.async_set("light.strip", "off", {"friendly_name": "Kitchen light strip"})
    async_expose_entity(hass, conversation.DOMAIN, "light.strip", False)
    mock_client.ask.return_value = build_response(**answer_set())
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    await converse(hass, text)
    await hass.async_block_till_done()

    assert bool(calls) is acts
    sent = mock_client.ask.call_args.args[0]
    assert "light.strip" not in str(sent)
    assert "Kitchen light strip" not in str(sent["entities"])
    if not acts:
        trace = house.runtime_data.conversation_traces[0]
        assert trace["reason"] == "named a device that is not exposed"


async def test_brightness_is_read_from_the_text_not_the_model(hass, house, mock_client):
    """Jev judges and does not calculate, so the number comes out of a regex."""
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(
                choice="set_brightness", probabilities={}, confidence=0.93
            )
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    await converse(hass, "set the kitchen light to 40 percent")
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].data["brightness_pct"] == 40


async def test_a_brightness_command_with_no_number_acts_on_nothing(
    hass, house, mock_client
):
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(
                choice="set_brightness", probabilities={}, confidence=0.93
            )
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    await converse(hass, "make the kitchen light brighter")
    await hass.async_block_till_done()
    assert calls == []


async def test_a_trace_records_what_was_decided(hass, house, mock_client):
    """A misrouted sentence is only fixable if you can see what was made of it."""
    mock_client.ask.return_value = build_response(**answer_set())
    await converse(hass, "kitchen light on")
    await hass.async_block_till_done()

    trace = house.runtime_data.conversation_traces[0]
    assert trace["text"] == "kitchen light on"
    assert trace["action"] == "turn_on"
    assert trace["slots"]["name"]["value"] == "Kitchen light"
    assert trace["exposed_entities"] == 2
    assert trace["input_tokens"] == 321
    assert trace["fallback"] is False


async def test_a_refusal_records_why(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        **answer_set(compound=NoulAnswer(noul=0.95))
    )
    await converse(hass, "two things at once")
    await hass.async_block_till_done()

    trace = house.runtime_data.conversation_traces[0]
    assert trace["fallback"] is True
    assert trace["reason"] == "several commands in one sentence"


async def test_traces_are_bounded(hass, house, mock_client):
    mock_client.ask.return_value = build_response(**answer_set())
    for i in range(CONVERSATION_TRACE_LENGTH + 5):
        await converse(hass, f"command {i}")
    await hass.async_block_till_done()

    assert len(house.runtime_data.conversation_traces) == CONVERSATION_TRACE_LENGTH


async def converse_in_a_pipeline(hass, text, conversation_id=None):
    """Converse the way assist_pipeline does, with a listener on the chat log."""
    deltas = []
    with (
        chat_session.async_get_chat_session(hass, conversation_id) as session,
        conversation.async_get_chat_log(
            hass,
            session,
            conversation.ConversationInput(
                text=text,
                context=Context(),
                conversation_id=session.conversation_id,
                device_id=None,
                satellite_id=None,
                language="en",
                agent_id=AGENT,
            ),
            chat_log_delta_listener=lambda _log, delta: deltas.append(delta),
        ) as chat_log,
    ):
        result = await conversation.async_converse(
            hass, text, session.conversation_id, Context(), "en", agent_id=AGENT
        )
        content = list(chat_log.content)
    return result, deltas, content


async def test_the_assist_dialog_shows_what_jev_answered(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        **answer_set(
            entity=ChoiceAnswer(
                choice="light.kitchen",
                probabilities={"light.kitchen": 0.8, "light.office": 0.2},
                confidence=0.8,
            )
        )
    )
    _, deltas, content = await converse_in_a_pipeline(hass, "kitchen light on")

    assert len(deltas) == 1
    assert deltas[0]["role"] == "assistant"
    shown = deltas[0]["thinking_content"]
    assert "Jev: turn_on, ok, confidence 0.97" in shown
    assert "entity: light.kitchen 0.80 (light.kitchen 0.80, light.office 0.20)" in shown
    assert "321 input tokens" in shown
    # Shown to the pipeline, never written into the conversation a fallback reads.
    assert [c.role for c in content] == ["system", "user"]


async def test_the_assist_dialog_shows_why_jev_refused(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        **answer_set(compound=NoulAnswer(noul=0.95))
    )
    result, deltas, _ = await converse_in_a_pipeline(hass, "two things at once")

    assert "several commands in one sentence" in deltas[0]["thinking_content"]
    assert "compound: 0.95" in deltas[0]["thinking_content"]
    assert result.response.response_type is ha_intent.IntentResponseType.ERROR


async def test_a_command_outside_a_pipeline_still_answers(hass, house, mock_client):
    """No listener, as from conversation.process: nothing to show it to."""
    mock_client.ask.return_value = build_response(**answer_set())
    result = await converse(hass, "kitchen light on")
    assert result.response.response_type is not ha_intent.IntentResponseType.ERROR


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("set the lamp to 40 percent", 40),
        ("set the lamp to 40%", 40),
        ("zet de lamp op 25 procent", 25),
        ("dim the lamp to 30", 30),
        ("turn on 2 lamps", None),
        ("set it to 400 percent", None),
        ("turn on the kitchen light", None),
    ],
)
def test_brightness_parsing(text, expected):
    assert find_brightness(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The word instead of the sign, one sentence per translated language. A
        # satellite that transcribes "prozent" must not lose the number.
        ("stelle die Lampe auf 40 Prozent", 40),
        ("mets la lampe a 40 pour cent", 40),
        ("imposta la lampada al 40 per cento", 40),
        ("pon la lampara al 40 por ciento", 40),
        ("coloque a lampada em 40 por cento", 40),
        ("ustaw lampe na 40 procent", 40),
        ("stall lampan pa 40 procent", 40),
        ("saet lampen til 40 procent", 40),
        ("nastav lampu na 40 procent", 40),
        ("установи лампу на 40 процентов", 40),
        # Chinese puts the marker in front of the number.
        ("把灯设为百分之40", 40),
        ("把灯设为 40%", 40),
        # A bare number, carried by a word about light level.
        ("dimme die Lampe auf 30", 30),
        ("tamise la lampe a 30", 30),
        ("attenua la lampada a 30", 30),
        ("atenúa la lámpara a 30", 30),
        ("escureça a lampada para 30", 30),
        ("przyciemnij lampe do 30", 30),
        ("dämpa lampan till 30", 30),
        ("dæmp lampen til 30", 30),
        ("ztlum lampu na 30", 30),
        ("приглуши лампу до 30", 30),
        ("把灯调暗到 30", 30),
        # Chinese writes no space in front of the number.
        ("把灯调暗到30", 30),
        ("把灯调亮到65", 65),
        # A bare number with nothing about light stays a count, in any language.
        ("zet de lamp op 2", None),
        ("accendi 2 lampade", None),
        ("stelle 2 Lampen an", None),
        # "hello" holds the German stem for bright. A satellite hears it often.
        ("hello, set the lamp to 2", None),
        ("stelle die Lampe heller auf 70", 70),
    ],
)
def test_brightness_parsing_in_every_translated_language(text, expected):
    assert find_brightness(text) == expected


async def test_a_state_question_answers_without_changing_anything(
    hass, house, mock_client
):
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="get_state", probabilities={}, confidence=0.91)
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    result = await converse(hass, "is the kitchen light on")
    await hass.async_block_till_done()

    assert calls == []
    # HassGetState finds the state and stops. The spoken sentence normally comes
    # from the default agent's templates, which this path never touches, so the
    # agent renders the same template itself.
    assert result.response.speech["plain"]["speech"] == "Kitchen light is off"


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        # The sentence comes from home-assistant-intents and the state word from
        # the light integration's own translations, so neither is written here.
        ("en", "Kitchen light is off"),
        ("nl", "Kitchen light is uit"),
        ("it", "Kitchen light \u00e8 spento"),
        ("de", "Kitchen light ist aus"),
        ("fr", "Kitchen light est \u00e9teint"),
        ("sv", "Kitchen light \u00e4r av"),
        ("da", "Kitchen light er fra"),
        ("cs", "Kitchen light je vypnuto"),
        # Polish inflects the adjective by the last letter of the device name, and
        # Russian writes the state word in Russian. Both templates compare the
        # state against the English word, so both are handed the raw state.
        ("pl", "Kitchen light jest wy\u0142\u0105czony"),
        ("ru", "\u0412\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u043e"),
        ("es", "El dispositivo Kitchen light est\u00e1 apagado"),
        # Brazilian Portuguese answers with the state alone, because the user
        # named the device in the question. That leaves the state word first, so
        # it is the word that gets the capital.
        ("pt-BR", "Desligado"),
        # Chinese writes no space around the copula.
        ("zh-Hans", "Kitchen light\u662f\u5173\u95ed"),
    ],
)
async def test_a_state_question_answers_in_the_pipeline_language(
    hass, house, mock_client, language, expected
):
    """The reply follows the language Assist is speaking, not this file's English.

    Reported on issue #9: Italian got "Luce Tavolo is off" from a pipeline that was
    answering in Italian everywhere else.
    """
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="get_state", probabilities={}, confidence=0.91)
        )
    )

    result = await converse(hass, "is the kitchen light on", language=language)
    await hass.async_block_till_done()

    assert result.response.speech["plain"]["speech"] == expected


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("en", "Kitchen light is off, Office light is on"),
        # Brazilian Portuguese drops the name, so listing two needs it back. The
        # name leads here, so the state word keeps its lower case.
        ("pt-BR", "Kitchen light: desligado, Office light: ligado"),
    ],
)
async def test_several_states_in_one_answer_keep_their_names(
    hass, house, language, expected
):
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen light"})
    hass.states.async_set("light.office", "on", {"friendly_name": "Office light"})
    matched = [hass.states.get("light.kitchen"), hass.states.get("light.office")]

    spoken = await _render_state_answer(hass, matched, [], language)

    assert spoken == expected


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("en", "Hall temperature is 21.5 \u00b0C"),
        # The German template writes the decimal comma and says the unit out loud.
        ("de", "Hall temperature ist 21,5 Grad"),
    ],
)
async def test_a_number_is_answered_with_its_unit(hass, house, language, expected):
    """The template reads state_with_unit, where the old sentence read state.

    No domain in snapshot.CONTROLLABLE carries a unit today, so the voice path
    cannot reach this yet. It pins the renderer so that adding one does not have
    to rediscover that the degrees were being dropped.
    """
    hass.states.async_set(
        "sensor.hall",
        "21.5",
        {"friendly_name": "Hall temperature", "unit_of_measurement": "\u00b0C"},
    )

    spoken = await _render_state_answer(
        hass, [hass.states.get("sensor.hall")], [], language
    )

    assert spoken == expected


async def test_a_state_question_in_a_language_with_no_template_still_answers(
    hass, house, mock_client
):
    """An English sentence beats silence when the intents package has no entry.

    Klingon is not one of the 90-odd languages home-assistant-intents ships.
    """
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="get_state", probabilities={}, confidence=0.91)
        )
    )

    result = await converse(hass, "is the kitchen light on", language="tlh")
    await hass.async_block_till_done()

    assert result.response.speech["plain"]["speech"] == "Kitchen light is off."


async def test_toggle_reaches_the_named_device(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="toggle", probabilities={}, confidence=0.92)
        )
    )
    calls = []
    hass.services.async_register("light", "toggle", lambda call: calls.append(call))

    await converse(hass, "flip the kitchen light")
    await hass.async_block_till_done()

    assert [c.data["entity_id"] for c in calls] == [["light.kitchen"]]


async def test_a_name_the_intent_layer_cannot_match_acts_on_nothing(
    hass, house, mock_client
):
    """A stale registry entry is a miss, not a crash, and nothing half-runs."""
    from custom_components.jev.snapshot import ExposedEntity

    mock_client.ask.return_value = build_response(**answer_set())
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    with patch(
        "custom_components.jev.conversation.async_snapshot",
        return_value=__import__(
            "custom_components.jev.snapshot", fromlist=["HomeSnapshot"]
        ).HomeSnapshot(
            entities=[
                ExposedEntity(
                    "light.kitchen", "A name nothing answers to", "light", None, "off"
                )
            ],
            areas=["Kitchen"],
            floors=[],
        ),
    ):
        result = await converse(hass, "kitchen light on")
    await hass.async_block_till_done()

    assert calls == []
    assert "did not understand" in result.response.speech["plain"]["speech"]


async def test_a_room_holding_nothing_exposed_is_not_offered(hass, house, mock_client):
    """Offering a room the agent cannot act in turns a right answer into a fallback.

    Measured on a real instance: the registry held rooms belonging to devices that
    were not exposed, and "kill the lights in the kitchen" came back as that room at
    0.98. The answer was right for the question asked and named somewhere holding
    nothing the agent could touch, so the intent matched nothing.
    """
    areas = ar.async_get(hass)
    areas.async_get_or_create("Utility room")
    mock_client.ask.return_value = build_response(**answer_set())
    mock_client.ask.reset_mock()

    await converse(hass, "kitchen light on")

    offered = mock_client.ask.call_args.args[1]["area"].criteria
    assert "Utility room" not in offered
    assert set(offered) == {"Kitchen", "Office", "none_of_these"}
    # The state carries the same list, so the model is never shown a room twice.
    assert mock_client.ask.call_args.args[0]["areas"] == ["Kitchen", "Office"]


async def test_a_whole_house_command_names_a_target_the_intent_accepts(
    hass, house, mock_client
):
    """Home Assistant requires one of name, area or floor, and reads "all" as every
    entity. Sending no target at all failed the slot check on a real instance:
    "turn everything off" answered "Sorry, that did not work" with the model right
    at 0.99.
    """
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="turn_off", probabilities={}, confidence=0.99),
            target_type=ChoiceAnswer(
                choice="everything", probabilities={}, confidence=0.95
            ),
            entity=ChoiceAnswer(choice="none_of_these", probabilities={}, confidence=0.9),
            # The kind is what makes "all" actionable, so this case supplies one.
            domain=ChoiceAnswer(choice="light", probabilities={}, confidence=0.93),
        )
    )
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen light"})
    calls = []
    hass.services.async_register("light", "turn_off", lambda call: calls.append(call))

    result = await converse(hass, "turn everything off")
    await hass.async_block_till_done()

    assert house.runtime_data.conversation_traces[0]["slots"]["name"]["value"] == "all"
    assert calls, "a whole-house command reached no entity"
    assert "did not work" not in (
        result.response.speech.get("plain", {}).get("speech", "")
    )


async def test_a_whole_house_command_with_no_kind_asks_which(hass, house, mock_client):
    """Home Assistant refuses "all" with no domain beside it, and so does this."""
    hass.states.async_set("switch.fan", "on", {"friendly_name": "Fan"})
    async_expose_entity(hass, conversation.DOMAIN, "switch.fan", True)
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="turn_off", probabilities={}, confidence=0.99),
            target_type=ChoiceAnswer(
                choice="everything", probabilities={}, confidence=0.95
            ),
            entity=ChoiceAnswer(choice="none_of_these", probabilities={}, confidence=0.9),
            domain=ChoiceAnswer(choice="none_of_these", probabilities={}, confidence=0.4),
        )
    )
    calls = []
    hass.services.async_register("light", "turn_off", lambda call: calls.append(call))

    result = await converse(hass, "turn everything off")
    await hass.async_block_till_done()

    assert calls == []
    assert "Which kind of thing" in result.response.speech["plain"]["speech"]


async def test_a_command_that_is_already_done_says_so(hass, house, mock_client):
    """A redundant command reads as a low-confidence one, and is not one.

    Measured on a real instance, three runs per starting state: the action scored
    1.00 with the light off and 0.25 to 0.31 with it on, while turn_on stayed the
    top option at 0.39 to 0.48. Refusing that as not understood answers the wrong
    thing to a sentence the model read correctly.
    """
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen light"})
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(
                choice="turn_on",
                probabilities={"turn_on": 0.44, "get_state": 0.31, "none_of_these": 0.25},
                confidence=0.28,
            )
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    result = await converse(hass, "could you put the kitchen light on please")
    await hass.async_block_till_done()

    assert calls == []
    assert result.response.speech["plain"]["speech"] == "Kitchen light is already on."


async def test_a_low_confidence_command_that_is_not_done_still_falls_back(
    hass, house, mock_client
):
    """The already-done path must not become a way around the confidence floor."""
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(
                choice="turn_on",
                probabilities={"turn_on": 0.44, "get_state": 0.31, "none_of_these": 0.25},
                confidence=0.28,
            )
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    # light.kitchen is off, so the command has something to do and is still unsure.
    result = await converse(hass, "mmh the kitchen thing")
    await hass.async_block_till_done()

    assert calls == []
    assert "did not understand" in result.response.speech["plain"]["speech"]


async def test_the_agent_answers_in_the_pipeline_language(hass, house, mock_client):
    """The intent layer localises its own replies. Ours have to be localised too."""
    mock_client.ask.return_value = build_response(
        **answer_set(compound=NoulAnswer(noul=0.97))
    )

    result = await conversation.async_converse(
        hass, "doe twee dingen tegelijk", None, Context(), language="nl", agent_id=AGENT
    )

    assert result.response.speech["plain"]["speech"] == "Sorry, dat begreep ik niet."


async def test_the_already_done_reply_is_translated_too(hass, house, mock_client):
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen light"})
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(
                choice="turn_on",
                probabilities={"turn_on": 0.44, "get_state": 0.31},
                confidence=0.28,
            )
        )
    )

    result = await conversation.async_converse(
        hass, "doe de keukenlamp aan", None, Context(), language="nl", agent_id=AGENT
    )

    assert result.response.speech["plain"]["speech"] == "Kitchen light staat al aan."


# One entity per controllable domain, and the service each one must end up calling.
# Home Assistant does not map on and off to turn_on and turn_off everywhere: a cover
# opens and closes, and a lock locks on turn_on, which is why lock is not a domain
# this agent offers at all. Without a case per domain the suite only ever exercised
# light, and the lock wording stayed wrong through five releases.
DOMAIN_CASES = [
    ("light.kitchen", "off", "turn_on", "turn_off"),
    ("switch.boiler", "off", "turn_on", "turn_off"),
    ("fan.bedroom", "off", "turn_on", "turn_off"),
    ("cover.blinds", "closed", "open_cover", "close_cover"),
    ("media_player.tv", "off", "turn_on", "turn_off"),
    ("climate.hallway", "off", "turn_on", "turn_off"),
    ("vacuum.robot", "docked", "turn_on", "turn_off"),
    ("input_boolean.guest_mode", "off", "turn_on", "turn_off"),
    ("scene.evening", "scening", "turn_on", None),
    ("script.bedtime", "off", "turn_on", "turn_off"),
]


async def one_device(hass, config_entry, entity_id, state):
    """A house holding the kitchen light and one other device, both exposed."""
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})

    entities = er.async_get(hass)
    for target, target_state in (("light.kitchen", "off"), (entity_id, state)):
        domain, object_id = target.split(".")
        entry = entities.async_get_or_create(
            domain, "demo", object_id, suggested_object_id=object_id
        )
        entities.async_update_entity(entry.entity_id, name=object_id.replace("_", " "))
        hass.states.async_set(target, target_state)
        async_expose_entity(hass, conversation.DOMAIN, target, True)

    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("entity_id", "state", "on_service", "off_service"), DOMAIN_CASES
)
async def test_each_controllable_domain_calls_the_right_service(
    hass, mock_client, config_entry, entity_id, state, on_service, off_service
):
    domain = entity_id.split(".")[0]
    await one_device(hass, config_entry, entity_id, state)

    calls: list[ServiceCall] = []
    for service in {on_service, off_service} - {None}:
        hass.services.async_register(domain, service, calls.append)

    for action, expected in (("turn_on", on_service), ("turn_off", off_service)):
        if expected is None:
            continue
        calls.clear()
        hass.states.async_set(entity_id, state)
        mock_client.ask.return_value = build_response(
            **answer_set(
                action=ChoiceAnswer(choice=action, probabilities={}, confidence=0.97),
                entity=ChoiceAnswer(choice=entity_id, probabilities={}, confidence=1.0),
                domain=ChoiceAnswer(choice=domain, probabilities={}, confidence=0.95),
            )
        )

        await converse(hass, f"{action} the {domain}")
        await hass.async_block_till_done()

        assert [c.service for c in calls] == [expected], (
            f"{action} on {entity_id} called {[c.service for c in calls]}"
        )


async def test_a_lock_is_never_offered_to_the_model(hass, mock_client, config_entry):
    """Home Assistant locks a lock on turn_on. The agent does not go near one."""
    await one_device(hass, config_entry, "switch.boiler", "off")
    hass.states.async_set("lock.front_door", "locked", {"friendly_name": "Front door"})
    async_expose_entity(hass, conversation.DOMAIN, "lock.front_door", True)

    mock_client.ask.return_value = build_response(**answer_set())
    await converse(hass, "unlock the front door")

    state = mock_client.ask.call_args.args[0]
    assert "lock.front_door" not in str(state)


async def test_a_house_with_no_areas_still_answers(hass, mock_client, config_entry):
    """Exposed entities, no room assigned to any of them.

    The area question then held nothing but none_of_these, and jevclient refuses a
    one-option choice, so every command raised ValueError before it reached the API.
    """
    await one_device(hass, config_entry, "switch.boiler", "off")

    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="turn_on", probabilities={}, confidence=0.97),
            entity=ChoiceAnswer(choice="switch.boiler", probabilities={}, confidence=1.0),
            domain=ChoiceAnswer(choice="switch", probabilities={}, confidence=0.95),
        )
    )
    calls: list[ServiceCall] = []
    hass.services.async_register("switch", "turn_on", calls.append)

    await converse(hass, "turn the boiler on")
    await hass.async_block_till_done()

    assert "area" not in mock_client.ask.call_args.args[1]
    assert [c.service for c in calls] == ["turn_on"]


@pytest.mark.parametrize(
    ("device_class", "state", "expected"),
    [
        # The device class is what makes a door open rather than switched on, and
        # a motion sensor detect rather than report itself as on.
        ("door", "on", "Front door \u00e8 aperto"),
        ("door", "off", "Front door \u00e8 chiuso"),
        ("motion", "on", "Front door \u00e8 rilevato"),
        # Without one, the domain default is the only word there is.
        (None, "on", "Front door \u00e8 acceso"),
    ],
)
async def test_the_state_word_follows_the_device_class(
    hass, house, device_class, state, expected
):
    """Home Assistant ships a word per device class, and this reads that layer."""
    assert await async_setup_component(hass, "binary_sensor", {})
    attributes = {"friendly_name": "Front door"}
    if device_class is not None:
        attributes["device_class"] = device_class
    hass.states.async_set("binary_sensor.front_door", state, attributes)

    spoken = await _render_state_answer(
        hass, [hass.states.get("binary_sensor.front_door")], [], "it"
    )

    assert spoken == expected


async def test_an_untranslated_language_still_gets_a_sentence(hass, house, mock_client):
    """This integration translates 13 languages. The intents package carries 63.

    Japanese is one of the other 50, so the English text was all it could get.
    """
    mock_client.ask.return_value = build_response(
        **answer_set(compound=NoulAnswer(noul=0.97))
    )

    result = await conversation.async_converse(
        hass, "一度に2つのことをして", None, Context(), language="ja", agent_id=AGENT
    )

    assert result.response.speech["plain"]["speech"] == (
        "すみません、理解できませんでした"
    )


async def test_a_whole_house_command_in_a_house_of_lights_turns_off_the_lights(
    hass, house, mock_client
):
    """With one kind of device exposed, asking which kind has one answer."""
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="turn_off", probabilities={}, confidence=0.99),
            target_type=ChoiceAnswer(
                choice="everything", probabilities={}, confidence=0.95
            ),
            entity=ChoiceAnswer(choice="none_of_these", probabilities={}, confidence=0.9),
            domain=ChoiceAnswer(choice="none_of_these", probabilities={}, confidence=0.4),
        )
    )
    calls = []
    hass.services.async_register("light", "turn_off", lambda call: calls.append(call))

    await converse(hass, "turn everything off")
    await hass.async_block_till_done()

    assert sorted(e for c in calls for e in c.data["entity_id"]) == [
        "light.kitchen",
        "light.office",
    ]


async def test_turning_a_room_off_never_unlocks_its_door(hass, house, mock_client):
    """Home Assistant maps turn_off on a lock to unlock, for every entity in the area.

    The model is never shown the lock, but an area command with no kind of device
    beside it reached every exposed entity in the room, the lock included.
    """
    kitchen = ar.async_get(hass).async_get_area_by_name("Kitchen")
    entry = er.async_get(hass).async_get_or_create(
        "lock", "demo", "back_door", suggested_object_id="back_door"
    )
    er.async_get(hass).async_update_entity(entry.entity_id, area_id=kitchen.id)
    hass.states.async_set("lock.back_door", "locked", {"friendly_name": "Back door"})
    async_expose_entity(hass, conversation.DOMAIN, "lock.back_door", True)

    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="turn_off", probabilities={}, confidence=0.99),
            target_type=ChoiceAnswer(choice="area", probabilities={}, confidence=0.95),
            entity=ChoiceAnswer(choice="none_of_these", probabilities={}, confidence=0.9),
            area=ChoiceAnswer(choice="Kitchen", probabilities={}, confidence=0.97),
            domain=ChoiceAnswer(choice="none_of_these", probabilities={}, confidence=0.4),
        )
    )
    unlocked, off = [], []
    hass.services.async_register("lock", "unlock", lambda call: unlocked.append(call))
    hass.services.async_register("light", "turn_off", lambda call: off.append(call))

    await converse(hass, "turn off the kitchen")
    await hass.async_block_till_done()

    assert unlocked == []
    assert [e for c in off for e in c.data["entity_id"]] == ["light.kitchen"]


async def test_a_garage_door_is_never_offered_to_the_model(
    hass, mock_client, config_entry
):
    """Opening a garage from a sentence matched at 0.6 is a way into the house."""
    await one_device(hass, config_entry, "cover.blinds", "closed")
    for entity_id, device_class in (
        ("cover.garage", "garage"),
        ("cover.drive", "gate"),
        ("cover.porch", "door"),
    ):
        hass.states.async_set(entity_id, "closed", {"device_class": device_class})
        async_expose_entity(hass, conversation.DOMAIN, entity_id, True)

    mock_client.ask.return_value = build_response(**answer_set())
    await converse(hass, "open the garage")

    sent = {e["entity_id"] for e in mock_client.ask.call_args.args[0]["entities"]}
    assert sent == {"light.kitchen", "cover.blinds"}


async def test_an_unsure_whole_house_command_does_nothing(hass, house, mock_client):
    """Two kinds of device, so the domain is asked and answered with confidence."""
    hass.states.async_set("switch.fan", "on", {"friendly_name": "Fan"})
    async_expose_entity(hass, conversation.DOMAIN, "switch.fan", True)
    hass.config_entries.async_update_entry(house, options={CONF_ALLOW_WHOLE_HOME: True})
    await hass.async_block_till_done()
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="turn_off", probabilities={}, confidence=0.99),
            target_type=ChoiceAnswer(
                choice="everything", probabilities={}, confidence=0.30
            ),
            entity=ChoiceAnswer(choice="none_of_these", probabilities={}, confidence=0.9),
            domain=ChoiceAnswer(choice="light", probabilities={}, confidence=0.95),
        )
    )
    calls = []
    hass.services.async_register("light", "turn_off", lambda call: calls.append(call))

    await converse(hass, "turn everything off")
    await hass.async_block_till_done()

    assert calls == []


async def test_a_command_that_is_probably_two_does_nothing(hass, house, mock_client):
    """At 0.79 the sentence is more likely two commands than one."""
    mock_client.ask.return_value = build_response(
        **answer_set(compound=NoulAnswer(noul=0.79))
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    await converse(hass, "kitchen light on and the office off")
    await hass.async_block_till_done()

    assert calls == []


async def test_no_matching_device_does_nothing(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        **answer_set(
            entity=ChoiceAnswer(choice="none_of_these", probabilities={}, confidence=0.9)
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    result = await converse(hass, "turn on the aquarium")
    await hass.async_block_till_done()

    assert calls == []
    assert "did not understand" in result.response.speech["plain"]["speech"]


async def test_two_devices_with_one_name_resolve_by_room(hass, house, mock_client):
    """Home Assistant tells duplicate names apart by area id, not by area name."""
    areas = ar.async_get(hass)
    registry = er.async_get(hass)
    for entity_id, area in (
        ("light.kitchen_ceiling", "Kitchen"),
        ("light.office_ceiling", "Office"),
    ):
        entry = registry.async_get_or_create(
            "light", "demo", entity_id.split(".")[1], suggested_object_id=entity_id[6:]
        )
        registry.async_update_entity(
            entry.entity_id, name="Ceiling", area_id=areas.async_get_area_by_name(area).id
        )
        hass.states.async_set(entity_id, "off", {"friendly_name": "Ceiling"})
        async_expose_entity(hass, conversation.DOMAIN, entity_id, True)

    mock_client.ask.return_value = build_response(
        **answer_set(
            entity=ChoiceAnswer(
                choice="light.office_ceiling", probabilities={}, confidence=1.0
            ),
            area=ChoiceAnswer(choice="Office", probabilities={}, confidence=0.95),
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    await converse(hass, "office ceiling on")
    await hass.async_block_till_done()

    assert [e for c in calls for e in c.data["entity_id"]] == ["light.office_ceiling"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("set lamp 2 brightness to 40", 40),
        ("dim bedroom 2 to 30", 30),
        ("zet lamp 3 helderheid op 70", 70),
        ("百分之30", 30),
        ("set it to 1000 percent", None),
        ("set it to 12.5 percent", None),
        ("0.5%", None),
        ("make it 20% brighter", None),
        ("dim it by 20", None),
    ],
)
def test_a_brightness_is_only_read_when_it_is_a_level(text, expected):
    """A relative change, a fraction or a room number is not a level to set."""
    assert find_brightness(text) == expected


async def test_a_room_past_the_entity_cap_is_not_offered(hass, config_entry):
    from custom_components.jev.snapshot import async_snapshot

    assert await async_setup_component(hass, "homeassistant", {})
    areas = ar.async_get(hass)
    registry = er.async_get(hass)
    for entity_id, area in (
        ("light.a", "Attic"),
        ("light.b", "Attic"),
        ("light.c", "Cellar"),
    ):
        entry = registry.async_get_or_create(
            "light", "demo", entity_id[6:], suggested_object_id=entity_id[6:]
        )
        registry.async_update_entity(
            entry.entity_id, area_id=areas.async_get_or_create(area).id
        )
        hass.states.async_set(entity_id, "off")
        async_expose_entity(hass, conversation.DOMAIN, entity_id, True)

    snapshot = async_snapshot(hass, 2)

    assert [e.entity_id for e in snapshot.entities] == ["light.a", "light.b"]
    assert snapshot.areas == ["Attic"]
    # Past the cap is as good as hidden: the model never saw it to pick it.
    assert snapshot.hidden_names == ["c"]


_ENTITY = {}
_AREA = {
    "target_type": ChoiceAnswer(choice="area", probabilities={}, confidence=0.94),
    "entity": ChoiceAnswer(choice="none_of_these", probabilities={}, confidence=0.9),
    "area": ChoiceAnswer(choice="Office", probabilities={}, confidence=0.93),
}
_ALL = {
    "target_type": ChoiceAnswer(choice="everything", probabilities={}, confidence=0.95),
    "entity": ChoiceAnswer(choice="none_of_these", probabilities={}, confidence=0.9),
}


@pytest.mark.parametrize(
    ("language", "service", "text", "answers"),
    [
        ("en", "turn_on", "turn on the kitchen light", _ENTITY),
        ("en", "turn_off", "turn off the lights in the office", _AREA),
        ("en", "turn_off", "turn off all the lights", _ALL),
        ("nl", "turn_on", "zet de kitchen light aan", _ENTITY),
        ("nl", "turn_off", "zet de lampen in de office uit", _AREA),
        ("de", "turn_on", "schalte kitchen light ein", _ENTITY),
        ("pl", "turn_off", "wyłącz światła w office", _AREA),
    ],
)
async def test_an_action_says_what_the_default_agent_says(
    hass, house, mock_client, language, service, text, answers
):
    """An action used to reply with no sentence, and the Assist dialog showed nothing.

    The reference is the default agent itself, on a sentence it matches, for the
    same command.
    """
    action = ChoiceAnswer(choice=service, probabilities={}, confidence=0.98)
    mock_client.ask.return_value = build_response(**answer_set(action=action, **answers))
    hass.services.async_register("light", service, lambda call: None)

    ours = await converse(hass, text, language=language)
    theirs = await converse(
        hass, text, agent_id="conversation.home_assistant", language=language
    )

    spoken = ours.response.speech["plain"]["speech"]
    assert spoken
    assert spoken == theirs.response.speech["plain"]["speech"]


@pytest.mark.parametrize(("language", "expected"), [("en", "Done."), ("nl", "Gedaan.")])
async def test_an_action_with_no_sentence_of_its_own_says_done(
    hass, house, mock_client, language, expected
):
    """home-assistant-intents writes nothing for a toggle."""
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="toggle", probabilities={}, confidence=0.92)
        )
    )
    hass.services.async_register("light", "toggle", lambda call: None)

    result = await converse(hass, "flip the kitchen light", language=language)

    assert result.response.speech["plain"]["speech"] == expected


async def test_a_brightness_command_says_it_was_set(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(
                choice="set_brightness", probabilities={}, confidence=0.95
            )
        )
    )
    hass.services.async_register("light", "turn_on", lambda call: None)

    result = await converse(hass, "set the kitchen light to 40%")

    assert result.response.speech["plain"]["speech"] == "Brightness set"


# --- asking which device ---


def unsure_between(first, second, first_share=0.5, second_share=0.45, **rest):
    """An answer set whose entity answer is split between two devices."""
    shares = {first: first_share, second: second_share, NONE: 0.05, **rest}
    return answer_set(
        entity=ChoiceAnswer(
            choice=first, probabilities=shares, confidence=max(shares.values())
        ),
        area=ChoiceAnswer(choice=NONE, probabilities={}, confidence=0.9),
    )


def rename(hass, entity_id, name):
    """Rename in the registry too, which is where the intent layer matches names."""
    er.async_get(hass).async_update_entity(entity_id, name=name)
    hass.states.async_set(entity_id, "off", {"friendly_name": name})


def reply(choice, confidence=0.95):
    return {"which": ChoiceAnswer(choice=choice, probabilities={}, confidence=confidence)}


async def converse_in(hass, text, conversation_id):
    return await conversation.async_converse(
        hass, text, conversation_id, Context(), language="en", agent_id=AGENT
    )


async def test_two_devices_that_fit_the_name_get_a_question(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        **unsure_between("light.kitchen", "light.office")
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    result = await converse(hass, "light on")
    await hass.async_block_till_done()

    assert calls == []
    assert result.continue_conversation is True
    assert result.conversation_id
    assert (
        result.response.speech["plain"]["speech"]
        == "Do you mean Kitchen light or Office light?"
    )


async def test_a_sure_answer_is_still_asked_about_when_the_name_is_shared(
    hass, house, mock_client
):
    """Measured: two lights called "Lamp", and the model gave one of them 1.00."""
    rename(hass, "light.kitchen", "Lamp")
    rename(hass, "light.office", "Lamp")
    mock_client.ask.return_value = build_response(**answer_set())
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    result = await converse(hass, "turn on the lamp")
    await hass.async_block_till_done()

    assert calls == []
    assert result.response.speech["plain"]["speech"] == (
        "Do you mean Lamp (Kitchen) or Lamp (Office)?"
    )


async def test_a_shared_name_is_asked_about_when_none_got_most_of_the_answer(
    hass, house, mock_client
):
    """Measured: none_of_these 0.55, one Lamp 0.44, the other 0.01."""
    rename(hass, "light.kitchen", "Lamp")
    rename(hass, "light.office", "Lamp")
    shares = {NONE: 0.55, "light.kitchen": 0.44, "light.office": 0.01}
    mock_client.ask.return_value = build_response(
        **answer_set(
            entity=ChoiceAnswer(choice=NONE, probabilities=shares, confidence=0.55),
            area=ChoiceAnswer(choice=NONE, probabilities={}, confidence=0.9),
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    result = await converse(hass, "turn on the lamp")
    await hass.async_block_till_done()

    assert calls == []
    assert result.response.speech["plain"]["speech"] == (
        "Do you mean Lamp (Kitchen) or Lamp (Office)?"
    )


async def test_a_hidden_name_is_not_asked_about_as_a_shared_one(hass, house, mock_client):
    rename(hass, "light.kitchen", "Lamp")
    rename(hass, "light.office", "Lamp")
    hass.states.async_set("light.desk", "off", {"friendly_name": "Desk lamp"})
    async_expose_entity(hass, conversation.DOMAIN, "light.desk", False)
    shares = {NONE: 0.55, "light.kitchen": 0.44, "light.office": 0.01}
    mock_client.ask.return_value = build_response(
        **answer_set(
            entity=ChoiceAnswer(choice=NONE, probabilities=shares, confidence=0.55),
            area=ChoiceAnswer(choice=NONE, probabilities={}, confidence=0.9),
        )
    )

    result = await converse(hass, "turn on the desk lamp")

    assert result.continue_conversation is False
    trace = house.runtime_data.conversation_traces[0]
    assert trace["reason"] == "named a device that is not exposed"


async def test_a_room_that_is_named_settles_a_shared_name(hass, house, mock_client):
    rename(hass, "light.kitchen", "Lamp")
    rename(hass, "light.office", "Lamp")
    mock_client.ask.return_value = build_response(
        **answer_set(
            entity=ChoiceAnswer(choice="light.office", probabilities={}, confidence=1.0),
            area=ChoiceAnswer(choice="Office", probabilities={}, confidence=0.99),
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    result = await converse(hass, "turn on the lamp in the office")
    await hass.async_block_till_done()

    assert result.continue_conversation is False
    assert [e for c in calls for e in c.data["entity_id"]] == ["light.office"]


def a_satellite_in(hass, house, area_name):
    """A voice device placed in the named area, as a satellite is."""
    area = ar.async_get(hass).async_get_area_by_name(area_name)
    assert area is not None
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=house.entry_id, identifiers={("test", area_name)}
    )
    devices.async_update_device(device.id, area_id=area.id)
    return device.id


async def test_the_room_a_satellite_is_in_settles_a_shared_name(hass, house, mock_client):
    """Home Assistant's own agent prefers the satellite's area, and so does Jev."""
    rename(hass, "light.kitchen", "Lamp")
    rename(hass, "light.office", "Lamp")
    mock_client.ask.return_value = build_response(**answer_set())
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    result = await conversation.async_converse(
        hass,
        "turn on the lamp",
        None,
        Context(),
        language="en",
        agent_id=AGENT,
        device_id=a_satellite_in(hass, house, "Office"),
    )
    await hass.async_block_till_done()

    assert result.continue_conversation is False
    assert [e for c in calls for e in c.data["entity_id"]] == ["light.office"]


async def test_a_satellite_entity_area_settles_a_shared_name_the_model_was_unsure_of(
    hass, house, mock_client
):
    rename(hass, "light.kitchen", "Lamp")
    rename(hass, "light.office", "Lamp")
    kitchen = ar.async_get(hass).async_get_area_by_name("Kitchen")
    assert kitchen is not None
    entities = er.async_get(hass)
    satellite = entities.async_get_or_create("assist_satellite", "test", "kitchen")
    entities.async_update_entity(satellite.entity_id, area_id=kitchen.id)
    shares = {NONE: 0.55, "light.office": 0.44, "light.kitchen": 0.01}
    mock_client.ask.return_value = build_response(
        **answer_set(
            entity=ChoiceAnswer(choice=NONE, probabilities=shares, confidence=0.55),
            area=ChoiceAnswer(choice=NONE, probabilities={}, confidence=0.9),
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    result = await conversation.async_converse(
        hass,
        "turn on the lamp",
        None,
        Context(),
        language="en",
        agent_id=AGENT,
        satellite_id=satellite.entity_id,
    )
    await hass.async_block_till_done()

    assert result.continue_conversation is False
    assert [e for c in calls for e in c.data["entity_id"]] == ["light.kitchen"]


async def test_a_satellite_in_another_room_still_gets_the_question(
    hass, house, mock_client
):
    rename(hass, "light.kitchen", "Lamp")
    rename(hass, "light.office", "Lamp")
    ar.async_get(hass).async_create("Hall")
    mock_client.ask.return_value = build_response(**answer_set())

    result = await conversation.async_converse(
        hass,
        "turn on the lamp",
        None,
        Context(),
        language="en",
        agent_id=AGENT,
        device_id=a_satellite_in(hass, house, "Hall"),
    )

    assert result.response.speech["plain"]["speech"] == (
        "Do you mean Lamp (Kitchen) or Lamp (Office)?"
    )


@pytest.mark.parametrize(
    ("text", "chosen"),
    [
        # The whole name said beats a name that only shares a word with it.
        ("turn on the lamp", "light.kitchen"),
        ("turn on the desk lamp", "light.office"),
    ],
)
async def test_the_whole_name_said_is_the_device_meant(
    hass, house, mock_client, text, chosen
):
    rename(hass, "light.kitchen", "Lamp")
    rename(hass, "light.office", "Desk lamp")
    mock_client.ask.return_value = build_response(
        **answer_set(entity=ChoiceAnswer(choice=chosen, probabilities={}, confidence=1.0))
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    await converse(hass, text)
    await hass.async_block_till_done()

    assert [e for c in calls for e in c.data["entity_id"]] == [chosen]


async def test_the_reply_runs_the_first_command_on_the_device_it_picks(
    hass, house, mock_client
):
    mock_client.ask.return_value = build_response(
        **unsure_between("light.kitchen", "light.office"), **reply("light.office")
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    asked = await converse(hass, "light on")
    await converse_in(hass, "the office one", asked.conversation_id)
    await hass.async_block_till_done()

    assert [e for c in calls for e in c.data["entity_id"]] == ["light.office"]
    # The reply was one small question about the two devices, not a new command.
    state, questions = mock_client.ask.await_args.args
    assert list(questions) == ["which", "new_request"]
    assert state == {"command": "light on", "reply": "the office one"}
    assert set(questions["which"].criteria) == {"light.kitchen", "light.office", NONE}


async def test_the_assist_dialog_shows_what_the_reply_picked(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        **unsure_between("light.kitchen", "light.office"), **reply("light.office")
    )
    hass.services.async_register("light", "turn_on", lambda call: None)

    asked = await converse(hass, "light on")
    result, deltas, _ = await converse_in_a_pipeline(
        hass, "the office one", asked.conversation_id
    )

    assert result.response.response_type is ha_intent.IntentResponseType.ACTION_DONE
    [delta] = deltas
    assert delta["thinking_content"].startswith(
        'Jev: a reply to "light on", picked light.office\n'
    )


async def test_a_reply_that_picks_neither_is_a_new_command(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        **unsure_between("light.kitchen", "light.office"), **reply(NONE)
    )
    asked = await converse(hass, "light on")
    mock_client.ask.reset_mock()

    result = await converse_in(hass, "never mind", asked.conversation_id)
    await hass.async_block_till_done()

    # The reply question, then the whole reply as a command of its own. It fits
    # no device's name, so it is not asked about again.
    assert mock_client.ask.await_count == 2
    assert "action" in mock_client.ask.await_args.args[1]
    assert result.continue_conversation is False


async def test_a_reply_that_names_a_device_in_a_new_command_runs_that_command(
    hass, house, mock_client
):
    """Measured: "never mind, turn off the lamp in the bedroom" picked that lamp."""
    new_request = {"new_request": NoulAnswer(noul=0.96)}
    turn_off = answer_set(
        action=ChoiceAnswer(choice="turn_off", probabilities={}, confidence=0.98),
        entity=ChoiceAnswer(choice="light.office", probabilities={}, confidence=0.97),
    )
    mock_client.ask.side_effect = [
        build_response(**unsure_between("light.kitchen", "light.office")),
        build_response(**reply("light.office"), **new_request),
        build_response(**turn_off),
    ]
    turned_on, turned_off = [], []
    hass.services.async_register("light", "turn_on", turned_on.append)
    hass.services.async_register("light", "turn_off", turned_off.append)

    asked = await converse(hass, "light on")
    await converse_in(hass, "no, turn off the office light", asked.conversation_id)
    await hass.async_block_till_done()

    assert turned_on == []
    assert [e for c in turned_off for e in c.data["entity_id"]] == ["light.office"]


async def test_an_unsure_reply_acts_on_nothing(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        **unsure_between("light.kitchen", "light.office"),
        **reply("light.office", confidence=0.4),
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    asked = await converse(hass, "light on")
    await converse_in(hass, "hmm", asked.conversation_id)
    await hass.async_block_till_done()

    assert calls == []


async def test_a_reply_in_another_conversation_is_not_an_answer(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        **unsure_between("light.kitchen", "light.office"), **reply("light.office")
    )
    await converse(hass, "light on")
    mock_client.ask.reset_mock()

    await converse(hass, "the office one")
    await hass.async_block_till_done()

    assert "which" not in mock_client.ask.await_args_list[0].args[1]


async def test_a_question_left_unanswered_expires_with_the_session(
    hass, house, mock_client
):
    mock_client.ask.return_value = build_response(
        **unsure_between("light.kitchen", "light.office"), **reply("light.office")
    )
    asked = await converse(hass, "light on")
    mock_client.ask.reset_mock()

    later = dt_util.utcnow() + CONVERSATION_TIMEOUT + timedelta(seconds=1)
    with patch("custom_components.jev.conversation.dt_util.utcnow", return_value=later):
        await converse_in(hass, "the office one", asked.conversation_id)
    await hass.async_block_till_done()

    assert "which" not in mock_client.ask.await_args_list[0].args[1]


async def test_a_third_device_in_the_running_means_no_question(hass, house, mock_client):
    hass.states.async_set("light.hall", "off", {"friendly_name": "Hall light"})
    async_expose_entity(hass, conversation.DOMAIN, "light.hall", True)
    mock_client.ask.return_value = build_response(
        **unsure_between(
            "light.kitchen", "light.office", 0.4, 0.3, **{"light.hall": 0.25}
        )
    )

    result = await converse(hass, "light on")

    assert result.continue_conversation is False
    assert "did not understand" in result.response.speech["plain"]["speech"]


async def test_two_devices_with_nothing_to_tell_them_apart_get_no_question(
    hass, house, mock_client
):
    kitchen = ar.async_get(hass).async_get_area_by_name("Kitchen")
    assert kitchen is not None
    er.async_get(hass).async_update_entity("light.office", area_id=kitchen.id)
    rename(hass, "light.office", "Kitchen light")
    mock_client.ask.return_value = build_response(
        **unsure_between("light.kitchen", "light.office")
    )

    result = await converse(hass, "light on")

    assert result.continue_conversation is False
    assert "did not understand" in result.response.speech["plain"]["speech"]


async def test_the_same_name_in_two_rooms_is_asked_by_room(hass, house, mock_client):
    rename(hass, "light.office", "Kitchen light")
    mock_client.ask.return_value = build_response(
        **unsure_between("light.kitchen", "light.office")
    )

    result = await converse(hass, "light on")

    assert result.response.speech["plain"]["speech"] == (
        "Do you mean Kitchen light (Kitchen) or Kitchen light (Office)?"
    )


async def test_the_question_is_asked_in_the_pipeline_language(hass, house, mock_client):
    rename(hass, "light.kitchen", "Lamp keuken")
    rename(hass, "light.office", "Lamp kantoor")
    mock_client.ask.return_value = build_response(
        **unsure_between("light.kitchen", "light.office")
    )

    result = await converse(hass, "lamp aan", language="nl")

    assert (
        result.response.speech["plain"]["speech"]
        == "Bedoel je Lamp keuken of Lamp kantoor?"
    )


async def test_a_reply_past_the_budget_is_refused_like_a_command(
    hass, house, mock_client
):
    mock_client.ask.return_value = build_response(
        **unsure_between("light.kitchen", "light.office"), **reply("light.office")
    )
    asked = await converse(hass, "light on")
    # Set on the account, not in the options: an options change reloads the entry,
    # and a reloaded agent holds no question to answer.
    house.runtime_data.usage.budget = 10
    mock_client.ask.reset_mock()

    result = await converse_in(hass, "the office one", asked.conversation_id)

    assert mock_client.ask.await_count == 0
    assert "budget is left" in result.response.speech["plain"]["speech"]
