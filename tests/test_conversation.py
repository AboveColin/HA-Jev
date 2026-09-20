"""The conversation agent: what it acts on, and what it refuses to act on.

Every test drives the real `conversation.async_converse` entry point and then
asserts on entity state, so what is checked is the effect on the house rather than
which helper got called.
"""

from unittest.mock import patch

import pytest
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, ServiceCall
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from jevclient import ChoiceAnswer, NoulAnswer

from custom_components.jev.const import (
    CONF_ALLOW_WHOLE_HOME,
    CONF_FALLBACK_AGENT,
    CONF_MIN_CONFIDENCE,
    CONVERSATION_TRACE_LENGTH,
)
from custom_components.jev.interpret import find_brightness

from .conftest import build_response

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
    return config_entry


async def converse(hass, text, agent_id=AGENT):
    return await conversation.async_converse(
        hass, text, None, Context(), language="en", agent_id=agent_id
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

    assert hass.states.get("sensor.jev_calls_today").state == "1"
    assert hass.states.get("sensor.jev_input_tokens_today").state == "321"


async def test_a_spent_budget_stops_voice_too(hass, house, mock_client):
    hass.config_entries.async_update_entry(house, options={"daily_token_budget": 10})
    await hass.async_block_till_done()
    house.runtime_data.usage.input_tokens = 999
    mock_client.ask.reset_mock()

    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))
    await converse(hass, "kitchen light on")
    await hass.async_block_till_done()

    assert mock_client.ask.await_count == 0
    assert calls == []


async def test_an_api_failure_acts_on_nothing(hass, house, mock_client):
    from jevclient import JevError

    mock_client.ask.side_effect = JevError("upstream is down")
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    result = await converse(hass, "kitchen light on")
    await hass.async_block_till_done()

    assert calls == []
    assert "did not understand" in result.response.speech["plain"]["speech"]


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
    # from the default agent's templates, which this path never touches.
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
