"""A conversation agent that routes spoken commands through Jev.

What this does that a sentence matcher cannot: it understands a command that was
not phrased the way the template expected. What it does that an LLM agent does not:
it runs one typed request, costs a fraction of a cent, and hands back a confidence
figure the router can refuse to act on.

Three things shape the design.

The house it sees is the Assist exposure list and nothing wider. The user already
decided which entities a voice assistant may touch.

Every command it understands runs a built-in intent, not a service call. Intents
carry Home Assistant's own matching, its own spoken responses in every supported
language, and its own permission checks. Reimplementing that would mean
reimplementing it worse.

Anything it is not sure about goes to the fallback agent whole, with no partial
action taken first. A voice assistant that half-acts is worse than one that says
it did not understand.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date
from typing import Literal

from homeassistant.components import conversation
from homeassistant.components.conversation.models import AbstractConversationAgent
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent as ha_intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from jevclient import JevAuthError, JevError

from .const import (
    CONF_ALLOW_WHOLE_HOME,
    CONF_FALLBACK_AGENT,
    CONF_MIN_CONFIDENCE,
    DEFAULT_MIN_CONFIDENCE,
    DOMAIN,
    MAX_CONVERSATION_ENTITIES,
)
from .coordinator import JevRuntimeData
from .entity import build_device_info
from .interpret import build_questions, interpret
from .snapshot import async_snapshot

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([JevConversationEntity(entry)])


class JevConversationEntity(conversation.ConversationEntity, AbstractConversationAgent):
    """Routes one sentence, then gets out of the way."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_conversation"
        runtime: JevRuntimeData = entry.runtime_data
        self._attr_device_info = build_device_info(entry.entry_id, runtime)

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Whatever the intents support.

        Jev reads the sentence and Home Assistant speaks the reply, so the limit is
        the intent layer's, not ours.
        """
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self._entry, self)

    async def async_will_remove_from_hass(self) -> None:
        conversation.async_unset_agent(self.hass, self._entry)
        await super().async_will_remove_from_hass()

    # --- options ---

    @property
    def _min_confidence(self) -> float:
        return float(self._entry.options.get(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE))

    @property
    def _fallback_agent(self) -> str | None:
        agent = self._entry.options.get(CONF_FALLBACK_AGENT)
        # Pointing the fallback at this entity would recurse until something gave
        # way. Refusing it here is cheaper than detecting the loop later.
        if agent in (None, "", self.entity_id):
            return None
        return str(agent)

    @property
    def _allow_whole_home(self) -> bool:
        return bool(self._entry.options.get(CONF_ALLOW_WHOLE_HOME, False))

    # --- the router ---

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        runtime: JevRuntimeData = self._entry.runtime_data

        # The budget covers voice as well as sensors, because a satellite that
        # mishears a wake word all night is exactly the runaway it exists to stop.
        runtime.usage.roll_over(date.today())
        if runtime.usage.would_exceed():
            return await self._fall_back(user_input, "the daily token budget is spent")

        snapshot = async_snapshot(self.hass, MAX_CONVERSATION_ENTITIES)
        if not snapshot.entities:
            return await self._fall_back(user_input, "no entities are exposed to Assist")

        questions = build_questions(user_input.text, snapshot, MAX_CONVERSATION_ENTITIES)
        state = snapshot.as_state() | {"command": user_input.text}

        try:
            response = await runtime.client.ask(state, questions)
        except JevAuthError as err:
            _LOGGER.error("TypeSafe rejected the API key: %s", err)
            return await self._fall_back(user_input, "the API key was rejected")
        except JevError as err:
            _LOGGER.warning("TypeSafe did not answer: %s", err)
            return await self._fall_back(user_input, f"TypeSafe did not answer: {err}")

        runtime.usage.record(response.usage.input_tokens)
        runtime.model_version = response.model or runtime.model_version
        runtime.usage.notify()

        decision = interpret(response, user_input.text, snapshot, self._min_confidence)
        runtime.conversation_traces.appendleft(
            {
                "text": user_input.text,
                "latency_ms": response.latency_ms,
                "input_tokens": response.usage.input_tokens,
                "exposed_entities": len(snapshot.entities),
                **asdict(decision),
            }
        )

        if decision.should_fall_back:
            return await self._fall_back(user_input, decision.reason)

        # The whole-home gate. "Turn everything off" is a real command and a
        # harmless one. "Turn everything on" at 3am, from a sentence the model was
        # only somewhat sure about, is not something to do silently. Off is allowed
        # because its worst case is a dark house; anything else asks first.
        if decision.targets_everything and not self._allow_whole_home:
            if decision.action != "turn_off":
                return self._speak(
                    user_input,
                    "That would affect the whole house. Say which room or which "
                    "device you mean.",
                )

        assert decision.intent_type is not None
        try:
            intent_response = await ha_intent.async_handle(
                self.hass,
                DOMAIN,
                decision.intent_type,
                decision.slots,
                user_input.text,
                user_input.context,
                language=user_input.language,
                assistant=conversation.DOMAIN,
                device_id=user_input.device_id,
                satellite_id=user_input.satellite_id,
                conversation_agent_id=user_input.agent_id,
            )
        except ha_intent.MatchFailedError as err:
            # The model named something the intent layer could not find. That is a
            # miss, not a failure, so the fallback agent gets the sentence intact.
            _LOGGER.debug("intent %s matched nothing: %s", decision.intent_type, err)
            return await self._fall_back(user_input, "the named target was not found")
        except ha_intent.IntentError as err:
            _LOGGER.error("intent %s failed: %s", decision.intent_type, err)
            return self._speak(user_input, "Sorry, that did not work.")

        _speak_the_answer(intent_response)
        return conversation.ConversationResult(
            response=intent_response, conversation_id=user_input.conversation_id
        )

    # --- the two ways out ---

    async def _fall_back(
        self, user_input: conversation.ConversationInput, why: str
    ) -> conversation.ConversationResult:
        """Hand the whole sentence to the configured agent, having done nothing."""
        agent = self._fallback_agent
        _LOGGER.debug("falling back to %s because %s", agent or "nobody", why)
        if agent is None:
            return self._speak(user_input, "Sorry, I did not understand that.")
        result = await conversation.async_converse(
            self.hass,
            user_input.text,
            user_input.conversation_id,
            user_input.context,
            language=user_input.language,
            agent_id=agent,
            device_id=user_input.device_id,
            satellite_id=user_input.satellite_id,
            extra_system_prompt=user_input.extra_system_prompt,
        )
        return result

    def _speak(
        self, user_input: conversation.ConversationInput, text: str
    ) -> conversation.ConversationResult:
        response = ha_intent.IntentResponse(language=user_input.language)
        response.async_set_speech(text)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )


def _speak_the_answer(response: ha_intent.IntentResponse) -> None:
    """Say what a state question found.

    `HassGetState` fills in the matched states and stops. The spoken sentence is
    normally written by the default agent's response templates, which only exist for
    sentences the default agent itself matched, so routing the intent here leaves a
    correct answer nobody hears. This writes one.
    """
    if response.response_type is not ha_intent.IntentResponseType.QUERY_ANSWER:
        return
    if response.speech:
        return
    matched = response.matched_states
    if not matched:
        response.async_set_speech("I could not find that.")
        return
    if len(matched) == 1:
        state = matched[0]
        response.async_set_speech(f"{state.name} is {state.state}.")
        return
    response.async_set_speech(
        ", ".join(f"{state.name} is {state.state}" for state in matched) + "."
    )
