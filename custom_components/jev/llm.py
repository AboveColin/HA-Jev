"""Jev's two judgements as tools for an LLM conversation agent.

An LLM agent reads the house through the Assist API and answers in prose. What it
cannot give is a probability it has been calibrated to: asked "is the washing
machine done", it says yes or no. These tools hand that one judgement to Jev and
return the number, so the agent can say how sure it is, or not act at all.

The tools are off unless the entry's options turn them on. Every tool's schema
goes into the prompt of every Assist LLM agent on every turn, which the user pays
for at that agent's provider whether a tool is called or not.

Each call goes through the matching action, so the daily budget, the error
messages and the usage sensors are the actions' own.
"""

from __future__ import annotations

from typing import Any, override

import voluptuous as vol
from homeassistant.components.homeassistant.exposed_entities import (
    async_should_expose,
)
from homeassistant.components.llm import LLMTools
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.llm import LLM_API_ASSIST, LLMContext, Tool, ToolInput
from homeassistant.util.json import JsonObjectType

from .const import (
    ATTR_CONFIG_ENTRY,
    CONF_INSTRUCTIONS,
    CONF_LLM_TOOLS,
    CONF_OPTIONS,
    CONF_STATE_TEMPLATE,
    DOMAIN,
    MAX_CONVERSATION_ENTITIES,
    SERVICE_CHOICE,
    SERVICE_NOUL,
)

_QUESTION = "question"
_FACTS = "facts"
_ACCOUNT = "account"

_QUESTION_DESCRIPTION = "The question, in plain words."
_FACTS_DESCRIPTION = (
    "Anything the question depends on that is not an entity state, such as what "
    "the user said. Optional."
)


class _JevTool(Tool):
    """One Jev action, judged against what Assist may see."""

    action: str

    def __init__(self, entries: dict[str, str], extra: dict[Any, Any]) -> None:
        # entries maps an entry title to its id. With two entries the model picks
        # one, because each has its own key and its own budget.
        self._entries = entries
        fields: dict[Any, Any] = {
            vol.Required(_QUESTION, description=_QUESTION_DESCRIPTION): str,
            **extra,
            vol.Optional(_FACTS, description=_FACTS_DESCRIPTION): str,
        }
        if len(entries) > 1:
            fields[
                vol.Required(_ACCOUNT, description="Which Jev account pays for this.")
            ] = vol.In(sorted(entries))
        self.parameters = vol.Schema(fields)

    def _data(
        self, hass: HomeAssistant, args: dict[str, Any], llm_context: LLMContext
    ) -> dict[str, Any]:
        account = args.get(_ACCOUNT) or next(iter(self._entries))
        data: dict[str, Any] = {
            CONF_INSTRUCTIONS: args[_QUESTION],
            ATTR_CONFIG_ENTRY: self._entries[account],
            ATTR_ENTITY_ID: _exposed(hass, llm_context),
        }
        if facts := args.get(_FACTS):
            # A mapping, because the action renders a string state as a template,
            # and a template can read entities that are not exposed to Assist.
            data[CONF_STATE_TEMPLATE] = {_FACTS: facts}
        return data

    async def _call(
        self, hass: HomeAssistant, data: dict[str, Any], llm_context: LLMContext
    ) -> dict[str, Any]:
        response = await hass.services.async_call(
            DOMAIN,
            self.action,
            data,
            blocking=True,
            context=llm_context.context,
            return_response=True,
        )
        assert response is not None
        return dict(response)


class NoulTool(_JevTool):
    name = f"{DOMAIN}__noul"
    description = (
        "Ask Jev how likely a yes/no statement about the home is, judged from the "
        "states of the entities exposed to Assist. Returns the probability that the "
        "answer is yes, from 0 to 1. Use it for a judgement, such as whether a "
        "machine is done or whether anyone is likely home, not for a state you can "
        "read directly."
    )
    action = SERVICE_NOUL

    def __init__(self, entries: dict[str, str]) -> None:
        super().__init__(entries, {})

    @override
    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> JsonObjectType:
        args = self.parameters(tool_input.tool_args)
        result = await self._call(hass, self._data(hass, args, llm_context), llm_context)
        return {"probability_yes": result["noul"]}


class ChoiceTool(_JevTool):
    name = f"{DOMAIN}__choice"
    description = (
        "Ask Jev which of a set of options best describes the home, judged from the "
        "states of the entities exposed to Assist. Returns the chosen option, the "
        "probability of every option, and a confidence from 0 to 1."
    )
    action = SERVICE_CHOICE

    def __init__(self, entries: dict[str, str]) -> None:
        super().__init__(
            entries,
            {
                vol.Required(CONF_OPTIONS, description="Two or more options."): vol.All(
                    [str], vol.Length(min=2)
                )
            },
        )

    @override
    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> JsonObjectType:
        args = self.parameters(tool_input.tool_args)
        data = self._data(hass, args, llm_context) | {CONF_OPTIONS: args[CONF_OPTIONS]}
        result = await self._call(hass, data, llm_context)
        return {
            "choice": result["choice"],
            "probabilities": result["probabilities"],
            "confidence": result["confidence"],
        }


def _exposed(hass: HomeAssistant, llm_context: LLMContext) -> list[str]:
    """The entities this assistant may see, capped as the voice agent caps them.

    Jev judges from what the user exposed to Assist and nothing wider, the same
    list the LLM agent itself reads.
    """
    exposed = sorted(
        state.entity_id
        for state in hass.states.async_all()
        if async_should_expose(hass, llm_context.assistant, state.entity_id)
    )
    return exposed[:MAX_CONVERSATION_ENTITIES]


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: LLMContext, api_id: str
) -> LLMTools | None:
    """The tools, for each loaded entry whose options turn them on."""
    if api_id != LLM_API_ASSIST:
        return None
    entries = {
        entry.title: entry.entry_id
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if entry.options.get(CONF_LLM_TOOLS, False)
    }
    if not entries:
        return None
    return LLMTools(tools=[NoulTool(entries), ChoiceTool(entries)])
