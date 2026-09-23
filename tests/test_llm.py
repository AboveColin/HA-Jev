"""Jev's judgements as tools for another LLM agent, through the Assist API."""

import pytest
import voluptuous as vol
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from jevclient import ChoiceAnswer, NoulAnswer
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jev.const import CONF_LLM_TOOLS, DOMAIN

from .conftest import PROBE_TOKENS, build_response


def assist_context() -> llm.LLMContext:
    return llm.LLMContext(
        platform="test",
        context=Context(),
        language="en",
        assistant=conversation.DOMAIN,
        device_id=None,
    )


async def tool_names(hass) -> list[str]:
    api = await llm.async_get_api(hass, llm.LLM_API_ASSIST, assist_context())
    return [tool.name for tool in api.tools]


async def call_tool(hass, name, args):
    api = await llm.async_get_api(hass, llm.LLM_API_ASSIST, assist_context())
    return await api.async_call_tool(llm.ToolInput(tool_name=name, tool_args=args))


@pytest.fixture
async def house(hass, mock_client, config_entry):
    """A loaded entry with the tools on, one exposed sensor and one private one."""
    assert await async_setup_component(hass, "conversation", {})
    assert await async_setup_component(hass, "llm", {})
    hass.states.async_set("sensor.washer_power", "1.2", {"unit_of_measurement": "W"})
    hass.states.async_set("lock.front_door", "unlocked")
    async_expose_entity(hass, conversation.DOMAIN, "sensor.washer_power", True)
    async_expose_entity(hass, conversation.DOMAIN, "lock.front_door", False)

    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={CONF_LLM_TOOLS: True})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    mock_client.ask.reset_mock()
    return config_entry


async def test_the_tools_are_off_until_the_options_turn_them_on(
    hass, mock_client, config_entry
):
    """Every tool schema is prompt text an agent's provider bills on every turn."""
    assert await async_setup_component(hass, "conversation", {})
    assert await async_setup_component(hass, "llm", {})
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert not [name for name in await tool_names(hass) if name.startswith("jev__")]

    hass.config_entries.async_update_entry(config_entry, options={CONF_LLM_TOOLS: True})
    await hass.async_block_till_done()
    assert {"jev__noul", "jev__choice"} <= set(await tool_names(hass))


async def test_another_api_gets_no_tools(hass, house):
    from custom_components.jev.llm import async_get_tools

    assert async_get_tools(hass, assist_context(), "some_other_api") is None


async def test_noul_judges_only_what_assist_may_see(hass, house, mock_client):
    mock_client.ask.return_value = build_response(answer=NoulAnswer(noul=0.83))
    result = await call_tool(
        hass, "jev__noul", {"question": "Is the washing machine done?"}
    )
    assert result == {"probability_yes": 0.83}

    state, questions = mock_client.ask.await_args.args
    assert [e["entity_id"] for e in state["entities"]] == ["sensor.washer_power"]
    assert questions["answer"].instructions == "Is the washing machine done?"
    assert "note" not in state


async def test_facts_are_sent_as_they_are_and_never_rendered(hass, house, mock_client):
    """A rendered template could read the lock, which is not exposed to Assist."""
    mock_client.ask.return_value = build_response(answer=NoulAnswer(noul=0.5))
    facts = "The user says: {{ states('lock.front_door') }}"
    await call_tool(hass, "jev__noul", {"question": "Is it done?", "facts": facts})

    state, _ = mock_client.ask.await_args.args
    assert state["note"] == {"facts": facts}
    assert "unlocked" not in str(state)


async def test_choice_returns_the_distribution(hass, house, mock_client):
    mock_client.ask.return_value = build_response(
        answer=ChoiceAnswer(
            choice="washing",
            probabilities={"washing": 0.9, "done": 0.1},
            confidence=0.8,
        )
    )
    result = await call_tool(
        hass,
        "jev__choice",
        {"question": "What is the washer doing?", "options": ["washing", "done"]},
    )
    assert result == {
        "choice": "washing",
        "probabilities": {"washing": 0.9, "done": 0.1},
        "confidence": 0.8,
    }
    _, questions = mock_client.ask.await_args.args
    assert set(questions["answer"].criteria) == {"washing", "done"}


async def test_a_choice_needs_two_options(hass, house, mock_client):
    with pytest.raises(vol.Invalid):
        await call_tool(
            hass, "jev__choice", {"question": "Which?", "options": ["only one"]}
        )
    assert mock_client.ask.await_count == 0


async def test_with_two_accounts_the_agent_names_the_one_that_pays(
    hass, house, mock_client
):
    """Each entry has its own key and budget, so neither is picked for the agent."""
    guest = MockConfigEntry(
        domain=DOMAIN,
        title="Jev guest",
        data={CONF_API_KEY: "another-key-not-a-real-one"},
        options={CONF_LLM_TOOLS: True},
        unique_id="fedcba9876543210",
    )
    guest.add_to_hass(hass)
    assert await hass.config_entries.async_setup(guest.entry_id)
    await hass.async_block_till_done()
    mock_client.ask.return_value = build_response(answer=NoulAnswer(noul=0.4))

    with pytest.raises(vol.Invalid):
        await call_tool(hass, "jev__noul", {"question": "Is it done?"})

    house_calls = house.runtime_data.usage.calls
    result = await call_tool(
        hass, "jev__noul", {"question": "Is it done?", "account": "Jev guest"}
    )
    assert result == {"probability_yes": 0.4}
    assert house.runtime_data.usage.calls == house_calls
    assert guest.runtime_data.usage.input_tokens == PROBE_TOKENS + 321
