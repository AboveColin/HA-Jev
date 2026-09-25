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

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

from homeassistant.components import conversation
from homeassistant.components.conversation.models import AbstractConversationAgent
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_CLASS, MATCH_ALL
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent as ha_intent
from homeassistant.helpers import template, translation
from homeassistant.helpers.chat_session import CONVERSATION_TIMEOUT
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from homeassistant.util import language as language_util
from jevclient import (
    Choice,
    ChoiceAnswer,
    JevAuthError,
    JevError,
    JevResponse,
    Noul,
    NoulAnswer,
    Question,
)

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
from .interpret import (
    ACTIONS,
    NONE,
    Interpretation,
    build_questions,
    interpret,
    level_questions,
    read_level,
    spoken_name,
)
from .payload import payload_bytes
from .snapshot import HomeSnapshot, async_heard_in, async_snapshot

_LOGGER = logging.getLogger(__name__)

# Used when a translation is missing, so a missing key is still a sentence rather
# than a blank reply. Kept in step with strings.json by a test.
_FALLBACK = {
    "not_understood": "Sorry, I did not understand that.",
    "whole_house": (
        "That would affect the whole house. Say which room or which device you mean."
    ),
    "which_kind": (
        "Which kind of thing do you mean? Say the lights, or the switches, or name "
        "a room."
    ),
    "intent_failed": "Sorry, that did not work.",
    "already_on": "{name} is already on.",
    "already_off": "{name} is already off.",
    "done": "Done.",
    "query_not_found": "I could not find that.",
    "budget_spent": (
        "Not enough of the daily token budget is left for that, so I cannot do it today."
    ),
    "auth_failed": "TypeSafe rejected the API key. Check it in the Jev settings.",
    "unavailable": "TypeSafe did not answer. Try again in a moment.",
    "which_device": "Do you mean {first} or {second}?",
}

PARALLEL_UPDATES = 0


@dataclass(slots=True)
class _Pending:
    """A command held while the agent asks which device it meant."""

    text: str
    response: JevResponse
    snapshot: HomeSnapshot
    candidates: tuple[str, str]
    expires: datetime = field(
        default_factory=lambda: dt_util.utcnow() + CONVERSATION_TIMEOUT
    )


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
        # Conversation id to the command waiting on its reply.
        self._pending: dict[str, _Pending] = {}

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
        if not agent or agent == self.entity_id:
            return None
        # Another Jev agent is the same loop one step removed: two entries that fall
        # back to each other pass the sentence between them and pay each time.
        registered = er.async_get(self.hass).async_get(agent)
        entry = self.hass.config_entries.async_get_entry(agent)
        if (registered and registered.platform == DOMAIN) or (
            entry and entry.domain == DOMAIN
        ):
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

        runtime.usage.roll_over(dt_util.now().date())
        if (pending := self._take_pending(chat_log.conversation_id)) is not None:
            resolved = await self._resolve(user_input, chat_log, pending)
            if resolved is not None:
                return resolved

        snapshot = async_snapshot(self.hass, MAX_CONVERSATION_ENTITIES)
        if not snapshot.entities:
            return await self._fall_back(user_input, "no entities are exposed to Assist")

        questions = build_questions(user_input.text, snapshot, MAX_CONVERSATION_ENTITIES)
        state = snapshot.as_state() | {"command": user_input.text}
        response = await self._ask(user_input, state, questions)
        if isinstance(response, conversation.ConversationResult):
            return response

        decision = interpret(
            response,
            user_input.text,
            snapshot,
            self._min_confidence,
            heard_in=async_heard_in(
                self.hass, user_input.satellite_id, user_input.device_id
            ),
        )
        self._trace(
            chat_log,
            response,
            {
                "text": user_input.text,
                "exposed_entities": len(snapshot.entities),
                **asdict(decision),
            },
        )

        if decision.candidates is not None:
            return await self._ask_which(
                user_input,
                chat_log,
                _Pending(user_input.text, response, snapshot, decision.candidates),
            )
        return await self._act(user_input, chat_log, decision, user_input.text)

    async def _ask(
        self,
        user_input: conversation.ConversationInput,
        state: dict[str, Any],
        questions: dict[str, Question],
    ) -> JevResponse | conversation.ConversationResult:
        """One call to Jev, inside the budget. A result means it did not answer."""
        runtime: JevRuntimeData = self._entry.runtime_data

        # The budget covers voice as well as sensors, because a satellite that
        # mishears a wake word all night is exactly the runaway it exists to stop.
        # The check is on this command's estimate, the same as a context's, so the
        # last command of the day cannot take the total past the budget.
        request_bytes = payload_bytes(state, questions, runtime.model)
        estimate = runtime.usage.estimate_tokens(request_bytes)
        if runtime.usage.would_exceed_with(estimate):
            return await self._fall_back(
                user_input,
                f"the daily token budget has {runtime.usage.remaining()} tokens left "
                f"and this command needs about {estimate}",
                "budget_spent",
            )

        try:
            with runtime.usage.reservation(estimate):
                response = await runtime.client.ask(state, questions)
        except JevAuthError as err:
            _LOGGER.error("TypeSafe rejected the API key: %s", err)
            self._entry.async_start_reauth(self.hass)
            return await self._fall_back(
                user_input, "the API key was rejected", "auth_failed"
            )
        except JevError as err:
            _LOGGER.warning("TypeSafe did not answer: %s", err)
            return await self._fall_back(
                user_input, f"TypeSafe did not answer: {err}", "unavailable"
            )

        runtime.usage.record(response.usage.input_tokens, request_bytes)
        runtime.model_version = response.model or runtime.model_version
        runtime.usage.notify()
        return response

    def _trace(
        self,
        chat_log: conversation.ChatLog,
        response: JevResponse,
        record: dict[str, Any],
    ) -> None:
        """Keep what one call decided, for diagnostics and the Assist dialog."""
        runtime: JevRuntimeData = self._entry.runtime_data
        trace = {
            "latency_ms": response.latency_ms,
            "input_tokens": response.usage.input_tokens,
            **record,
        }
        runtime.conversation_traces.appendleft(trace)
        # The pipeline records a chat log delta as an intent-progress event: the
        # Assist dialog shows its thinking_content under the reply, and the run's
        # debug events keep it. The delta goes to the listener only, not into the
        # log, so a fallback agent reading this conversation never takes it for
        # something said. It carries each answer's distribution, because "why did
        # it pick the office light" is answered by the entity question and by
        # nothing in the decision alone.
        if chat_log.delta_listener is not None:
            chat_log.delta_listener(
                chat_log,
                {"role": "assistant", "thinking_content": _reasoning(trace, response)},
            )

    # --- asking which device ---

    def _take_pending(self, conversation_id: str) -> _Pending | None:
        """The command waiting on this conversation's reply, if it is still live.

        A pending command lasts as long as Home Assistant keeps the chat session,
        so a reply that arrives in a new session is never read as an answer.
        """
        now = dt_util.utcnow()
        for key in [k for k, v in self._pending.items() if v.expires < now]:
            del self._pending[key]
        return self._pending.pop(conversation_id, None)

    async def _ask_which(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        pending: _Pending,
    ) -> conversation.ConversationResult:
        """Ask which of two devices the command meant, and keep the command."""
        first, second = (pending.snapshot.by_id(e) for e in pending.candidates)
        assert first is not None and second is not None
        names = spoken_name(first, second)
        assert names is not None
        language = user_input.language or self.hass.config.language
        text = (await self._lines(language))["which_device"].format(
            first=names[0], second=names[1]
        )
        self._pending[chat_log.conversation_id] = pending
        # In the chat log, the next turn in this conversation carries the question
        # it answers, and an LLM fallback agent reads the same history.
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=self.entity_id, content=text)
        )
        response = ha_intent.IntentResponse(language=user_input.language)
        response.async_set_speech(text)
        # A satellite opens the microphone again for the answer.
        return conversation.ConversationResult(
            response=response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=True,
        )

    async def _resolve(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        pending: _Pending,
    ) -> conversation.ConversationResult | None:
        """Run the kept command on the device the reply picked.

        None when the reply picked neither, so the reply is handled as a new
        command: "no, the kitchen light" and "never mind, lock up" both are one.
        """
        snapshot = pending.snapshot
        options: dict[str, Any] = {
            entity_id: described.as_option()
            for entity_id in pending.candidates
            if (described := snapshot.by_id(entity_id)) is not None
        }
        options[NONE] = "Neither of these, or a different request"
        questions: dict[str, Question] = {
            "which": Choice("Which device does the reply pick?", options),
            # "Never mind, turn off the lamp in the bedroom" names one of the two, so
            # the choice alone picks it and the kept command, turn on, runs on it.
            # Measured on a development instance, twelve replies to "turn on the
            # lamp": the six that only pick a device scored 0.08 to 0.26 here, and
            # the six that ask for something else, that one included, 0.91 to 0.97.
            "new_request": Noul(
                "Does the reply ask for something of its own, rather than only "
                "saying which device the command meant?",
                true="The reply is a new instruction, or changes what should happen",
                false="The reply only picks a device, however it is phrased",
            ),
        }
        state = {"command": pending.text, "reply": user_input.text}
        response = await self._ask(user_input, state, questions)
        if isinstance(response, conversation.ConversationResult):
            return response

        answer = response.answers.get("which")
        new_request = response.answers.get("new_request")
        picked = (
            answer.choice
            if isinstance(answer, ChoiceAnswer)
            and answer.choice in pending.candidates
            and answer.confidence >= self._min_confidence
            and not (isinstance(new_request, NoulAnswer) and new_request.noul >= 0.5)
            else None
        )
        self._trace(
            chat_log,
            response,
            {"text": user_input.text, "answers_command": pending.text, "picked": picked},
        )
        if picked is None:
            return None

        # The first call's answers stand, with the entity question settled. The
        # command is read from its own sentence again, so a brightness it named
        # still comes from the text.
        settled = ChoiceAnswer(choice=picked, probabilities={picked: 1.0}, confidence=1.0)
        first = replace(
            pending.response, answers=pending.response.answers | {"entity": settled}
        )
        decision = interpret(
            first, pending.text, snapshot, self._min_confidence, ask_back=False
        )
        return await self._act(user_input, chat_log, decision, pending.text)

    # --- a level said in words ---

    async def _ask_level(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        decision: Interpretation,
        text: str,
    ) -> Interpretation | conversation.ConversationResult:
        """Ask for the level of "set the lamp to forty percent", one more request.

        Only a brightness command with no digit in it sends this, so no other
        command pays for it. It sends the sentence alone: 403 to 568 input tokens.
        """
        response = await self._ask(user_input, {"command": text}, level_questions())
        if isinstance(response, conversation.ConversationResult):
            return response
        level = read_level(response, text, self._min_confidence)
        self._trace(chat_log, response, {"text": text, "level_for": level})
        if level is None:
            return replace(
                decision,
                fallback=True,
                reason="a brightness was asked for but no level was said",
                needs_level=False,
            )
        slots = decision.slots | {"brightness": {"value": level}}
        return replace(decision, slots=slots, needs_level=False)

    # --- acting ---

    async def _act(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        decision: Interpretation,
        text: str,
    ) -> conversation.ConversationResult:
        """Carry out one decision, or say why not."""
        if decision.already_satisfied is not None:
            name, settled = decision.already_satisfied
            return await self._speak(user_input, f"already_{settled}", name=name)

        if decision.needs_level:
            read = await self._ask_level(user_input, chat_log, decision, text)
            if isinstance(read, conversation.ConversationResult):
                return read
            decision = read

        if decision.should_fall_back:
            return await self._fall_back(user_input, decision.reason)

        # The whole-home gate. "Turn everything off" is a real command and a
        # harmless one. "Turn everything on" at 3am, from a sentence the model was
        # only somewhat sure about, is not something to do silently. Off is allowed
        # because its worst case is a dark house; anything else asks first.
        if decision.targets_everything:
            if not self._allow_whole_home and decision.action != "turn_off":
                return await self._speak(user_input, "whole_house")
            # Home Assistant refuses "all" with no kind of device beside it, and an
            # unbounded command is not something to infer from one sentence anyway.
            if "domain" not in decision.slots:
                return await self._speak(user_input, "which_kind")

        assert decision.intent_type is not None
        try:
            intent_response = await ha_intent.async_handle(
                self.hass,
                DOMAIN,
                decision.intent_type,
                decision.slots,
                text,
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
        except ha_intent.IntentHandleError as err:
            # Home Assistant raises this only when no entity succeeded, so nothing
            # changed. A media player with no turn_off does this, and the fallback
            # agent may know another way to do what was asked.
            _LOGGER.debug("intent %s failed: %s", decision.intent_type, err)
            return await self._fall_back(
                user_input, "the intent failed on every target", "intent_failed"
            )
        except ha_intent.IntentError as err:
            _LOGGER.error("intent %s failed: %s", decision.intent_type, err)
            # An error, so a satellite does not hear it as a command that went through.
            return await self._speak(
                user_input,
                "intent_failed",
                error=ha_intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
            )

        # Loading our lines reads translations, so only a reply without a sentence of
        # its own loads them.
        language = user_input.language or self.hass.config.language
        if intent_response.response_type is ha_intent.IntentResponseType.QUERY_ANSWER:
            await _speak_the_answer(
                self.hass, intent_response, language, await self._lines(language)
            )
        elif (
            intent_response.response_type is ha_intent.IntentResponseType.ACTION_DONE
            and not intent_response.speech
        ):
            spoken = await _render_action_answer(
                self.hass, intent_response, decision.intent_type, decision.slots, language
            )
            intent_response.async_set_speech(
                spoken or (await self._lines(language))["done"]
            )
        return conversation.ConversationResult(
            response=intent_response, conversation_id=user_input.conversation_id
        )

    # --- the two ways out ---

    async def _fall_back(
        self,
        user_input: conversation.ConversationInput,
        why: str,
        line: str = "not_understood",
    ) -> conversation.ConversationResult:
        """Hand the whole sentence to the configured agent, having done nothing.

        With no agent to hand it to, say why, so a spent budget or a rejected key
        is not heard as a sentence the model failed to understand.
        """
        agent = self._fallback_agent
        _LOGGER.debug("falling back to %s because %s", agent or "nobody", why)
        if agent is None:
            # An error, as the default agent answers one. A satellite and the Assist
            # dialog treat an action_done reply as a command that went through.
            code = (
                ha_intent.IntentResponseErrorCode.NO_INTENT_MATCH
                if line == "not_understood"
                else ha_intent.IntentResponseErrorCode.FAILED_TO_HANDLE
            )
            return await self._speak(user_input, line, error=code)
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

    async def _lines(self, language: str) -> dict[str, str]:
        """This agent's own lines, in the language the pipeline is speaking.

        The intent layer localises its own replies, so anything this agent says
        itself has to be localised here or a Dutch pipeline answers in English.
        The English text is the last resort, so a missing key is still a sentence.

        This integration translates 13 languages. Home Assistant words two of these
        lines in all 63 that home-assistant-intents carries, so a pipeline speaking
        one of the other 50 hears a sentence rather than English. Our own wording
        wins wherever we have it.
        """
        ours = await translation.async_get_translations(
            self.hass, language, "common", [DOMAIN]
        )
        # The cache loads English underneath every language, so a language this
        # integration has not translated comes back as its English text rather than
        # missing. Two identical dictionaries is what tells those apart.
        shipped = None
        if not language_util.matches(language, {"en"}) and ours == (
            await translation.async_get_translations(self.hass, "en", "common", [DOMAIN])
        ):
            shipped = await _shipped(self.hass, language)
        lines = {}
        for key, fallback in _FALLBACK.items():
            text = None
            if shipped is not None and (name := _SHIPPED_SENTENCE.get(key)):
                text = shipped.errors.get(name)
            lines[key] = text or ours.get(f"component.{DOMAIN}.common.{key}", fallback)
        return lines

    async def _speak(
        self,
        user_input: conversation.ConversationInput,
        key: str,
        error: ha_intent.IntentResponseErrorCode | None = None,
        **placeholders: str,
    ) -> conversation.ConversationResult:
        """Say one of our own lines, with its placeholders filled in."""
        language = user_input.language or self.hass.config.language
        text = (await self._lines(language))[key].format(**placeholders)
        response = ha_intent.IntentResponse(language=user_input.language)
        if error is None:
            response.async_set_speech(text)
        else:
            response.async_set_error(error, text)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )


def _reasoning(trace: Mapping[str, Any], response: JevResponse) -> str:
    """The trace as lines a person reads in the Assist dialog."""
    if "level_for" in trace:
        lines = [f"Jev: the level said in words, {trace['level_for'] or 'not read'}"]
    elif "answers_command" in trace:
        lines = [
            f'Jev: a reply to "{trace["answers_command"]}", '
            f"picked {trace['picked'] or 'neither'}"
        ]
    else:
        lines = [
            f"Jev: {trace['action'] or 'no action'}, {trace['reason']}, "
            f"confidence {trace['confidence']:.2f}",
            f"Slots: {json.dumps(trace['slots'], ensure_ascii=False)}",
        ]
    for key, answer in response.answers.items():
        if isinstance(answer, ChoiceAnswer):
            ranked = sorted((answer.probabilities or {}).items(), key=lambda kv: -kv[1])[
                :3
            ]
            spread = ", ".join(f"{k} {p:.2f}" for k, p in ranked)
            lines.append(f"{key}: {answer.choice} {answer.confidence:.2f} ({spread})")
        elif isinstance(answer, NoulAnswer):
            lines.append(f"{key}: {answer.noul:.2f}")
        else:
            lines.append(f"{key}: {json.dumps(asdict(answer), ensure_ascii=False)}")
    footer = f"{response.model}, {trace['input_tokens']} input tokens, "
    footer += f"{trace['latency_ms']:.0f} ms"
    if "exposed_entities" in trace:
        footer += f", {trace['exposed_entities']} entities"
    lines.append(footer)
    return "\n".join(lines)


# A template that compares the state against an English word writes the state word
# itself, in its own language. Hand it a translated one and every branch falls
# through. Measured against home-assistant-intents 2026.8.28: 3 of the 47 templates
# do this, Polish, Russian and Thai.
_COMPARES_STATE = re.compile(r"""==\s*['"](?:on|off|open|closed|locked|unlocked)['"]""")

# Home Assistant words these two in all 63 languages the intents package carries,
# against the 13 this integration translates. Saying what stock Assist says is the
# same choice the state answer makes.
_SHIPPED_SENTENCE = {"not_understood": "no_intent", "intent_failed": "handle_error"}


@dataclass(frozen=True, slots=True)
class _Shipped:
    """One language's share of home-assistant-intents."""

    state_answer: str | None
    writes_the_state_word: bool
    errors: Mapping[str, str]
    # The sentences for each action intent, keyed by the response name the default
    # agent's own sentence data picks.
    action_answers: Mapping[str, Mapping[str, str]]


# Read once per language, keyed by the language that was asked for rather than the
# variant it matched, so "it" and "it-IT" cost one load each. None means the
# package carries nothing for it and this agent falls back to English.
_SHIPPED: dict[str, _Shipped | None] = {}


def _load_shipped(language: str) -> _Shipped | None:
    """Read one language out of home-assistant-intents, in the executor.

    The package ships the answer template for a state question and the error
    sentences, both written by the people who translate the rest of Assist. Polish
    inflects the adjective by the last letter of the device name, Russian writes the
    state word in Russian, and German turns 21.5 into 21,5 Grad. None of that could
    be got right from a string table of this integration's own, and all of it reaches
    languages this integration does not translate.
    """
    try:
        from home_assistant_intents import (
            get_intents,
            get_languages,
        )
    except ImportError:  # pragma: no cover - the conversation integration installs it
        return None
    matches = language_util.matches(language, set(get_languages()))
    if not matches:
        return None
    intents = get_intents(matches[0])
    if not intents:
        return None
    responses = intents.get("responses", {})
    answer = responses.get("intents", {}).get("HassGetState", {}).get("one")
    if not isinstance(answer, str):
        answer = None
    return _Shipped(
        state_answer=answer,
        writes_the_state_word=bool(answer and _COMPARES_STATE.search(answer)),
        errors={
            key: text
            for key, text in responses.get("errors", {}).items()
            if isinstance(text, str) and text.strip()
        },
        action_answers={
            intent_type: {
                key: text
                for key, text in (
                    responses.get("intents", {}).get(intent_type) or {}
                ).items()
                if isinstance(text, str) and text.strip()
            }
            for intent_type in ACTIONS.values()
        },
    )


async def _shipped(hass: HomeAssistant, language: str) -> _Shipped | None:
    """What home-assistant-intents carries for a language, loaded once."""
    if language not in _SHIPPED:
        _SHIPPED[language] = await hass.async_add_executor_job(_load_shipped, language)
    return _SHIPPED[language]


async def _state_word(hass: HomeAssistant, state: State, language: str) -> str | None:
    """Home Assistant's own word for this state, or None when it has none.

    The three keys `async_translate_state` reads, in its order: the entity's own
    translation key, then the device class, then the domain's default. That helper
    cannot be called here, because it reads `hass.config.language`, which is the
    language of the user interface and not the one this pipeline speaks.

    The device class layer is what makes a door answer "aperto" rather than
    "acceso", and a motion sensor "rilevato" rather than "on".

    Lower case, because the shipped words are interface labels and are capitalised
    for a badge. The finished sentence gets its first letter back below.
    """
    domain = state.domain
    entry = er.async_get(hass).async_get(state.entity_id)
    if entry is not None and entry.translation_key is not None:
        own = await translation.async_get_translations(
            hass, language, "entity", {entry.platform}
        )
        key = (
            f"component.{entry.platform}.entity.{domain}"
            f".{entry.translation_key}.state.{state.state}"
        )
        if word := own.get(key):
            return word.lower()
    words = await translation.async_get_translations(
        hass, language, "entity_component", {domain}
    )
    if (device_class := state.attributes.get(ATTR_DEVICE_CLASS)) is not None:
        key = f"component.{domain}.entity_component.{device_class}.state.{state.state}"
        if word := words.get(key):
            return word.lower()
    word = words.get(f"component.{domain}.entity_component._.state.{state.state}")
    return word.lower() if word else None


class _SpokenState(template.TemplateState):
    """A state whose `state_with_unit` reads in the language being spoken.

    The answer templates reach the state only through this property, so one
    substitution here turns "Luce Tavolo è off" into "Luce Tavolo è spento" without
    touching the sentence. A numeric state has no translation, so a sensor keeps
    its rounded value and its unit.
    """

    __slots__ = ("_word",)

    def __init__(self, hass: HomeAssistant, state: State, word: str | None) -> None:
        """Carry the translated word, or None to leave the state as it is."""
        super().__init__(hass, state)
        self._word = word

    @property
    def state_with_unit(self) -> str:
        """The state in the spoken language, or Home Assistant's own formatting."""
        if self._word is None:
            return super().state_with_unit
        return self._word


async def _render_state_answer(
    hass: HomeAssistant,
    matched: list[State],
    unmatched: list[State],
    language: str,
) -> str | None:
    """The sentence the default agent would have said, or None if it cannot."""
    shipped = await _shipped(hass, language)
    if shipped is None or shipped.state_answer is None:
        return None
    words: dict[str, str | None] = {}
    if not shipped.writes_the_state_word:
        for state in (*matched, *unmatched):
            words[state.entity_id] = await _state_word(hass, state, language)

    def spoken(state: State) -> _SpokenState:
        return _SpokenState(hass, state, words.get(state.entity_id))

    answer = template.Template(shipped.state_answer, hass)
    query = {
        "matched": [spoken(state) for state in matched],
        "unmatched": [spoken(state) for state in unmatched],
    }
    parts = []
    for state in matched:
        try:
            rendered = answer.async_render(
                {
                    "slots": {"name": state.name},
                    "state": spoken(state),
                    "query": query,
                },
                parse_result=False,
            )
        except TemplateError as err:
            _LOGGER.debug("the %s state answer did not render: %s", language, err)
            return None
        # The templates are written over several lines and indented. The default
        # agent collapses that the same way before speaking it.
        sentence = " ".join(str(rendered).split())
        if not sentence:
            continue
        # Brazilian Portuguese answers with the state alone and Russian answers
        # "Выключено", both without the name, because the default agent reaches this
        # template only after the user named one device. Several answers in a row
        # need the name back or they say nothing about which device is which.
        if len(matched) > 1 and state.name.casefold() not in sentence.casefold():
            sentence = f"{state.name}: {sentence}"
        # Every template capitalises the device name, so a sentence that starts with
        # the state word instead, as the Brazilian Portuguese one does, would start
        # in lower case. Only the first character moves: `str.capitalize` would lower
        # the rest of the sentence.
        parts.append(sentence[0].upper() + sentence[1:])
    return ", ".join(parts) if parts else None


# One kind of device across a room or the whole house. The names differ between
# languages: English writes light_all and Polish lights_all.
_AREA_RESPONSES = {"light": ("lights_area",), "fan": ("fans_area",)}
_ALL_RESPONSES = {"light": ("light_all", "lights_all"), "fan": ("fan_all",)}

_SLOT_REFERENCE = re.compile(r"slots\.(\w+)")


def _response_keys(
    intent_type: str, slots: Mapping[str, Any], domain: str | None
) -> tuple[str, ...]:
    """The response names to try, closest first, for what this command did.

    The default agent reads the name from the sentence it matched. This agent has
    no sentence, so it reads the same thing off the slots it sent.
    """
    if intent_type == "HassLightSet":
        return ("brightness",)
    kinds = slots.get("domain", {}).get("value") or []
    kind = kinds[0] if len(kinds) == 1 else None
    closest: tuple[str, ...]
    if "area" in slots:
        closest = _AREA_RESPONSES.get(kind or "", ())
    elif slots.get("name", {}).get("value") == "all":
        closest = _ALL_RESPONSES.get(kind or "", ())
    elif domain is not None:
        # A scene is activated rather than turned on, and German says "Licht
        # eingeschaltet" for one light, where English has no sentence of its own.
        closest = (domain,)
    else:
        closest = ()
    return (*closest, "default")


async def _render_action_answer(
    hass: HomeAssistant,
    response: ha_intent.IntentResponse,
    intent_type: str | None,
    slots: Mapping[str, Any],
    language: str,
) -> str | None:
    """The sentence the default agent says after the same action, or None.

    `async_handle` does the action and says nothing, and the Assist dialog shows no
    reply at all for an empty one. The default agent's words come from the same
    package as the state answers, written by the people who translate Assist.
    None when the package has nothing that fits, and the agent says "Done." instead.
    """
    shipped = await _shipped(hass, language)
    if shipped is None or intent_type is None:
        return None
    answers = shipped.action_answers.get(intent_type, {})
    states = [*response.matched_states, *response.unmatched_states]
    first = states[0] if states else None
    # The name slot is the device's own name, as the model picked it. "all" is not a
    # name to say back.
    speech_slots = {
        key: value["value"]
        for key, value in slots.items()
        if key in ("name", "area")
        and isinstance(value.get("value"), str)
        and value["value"] != "all"
    } | response.speech_slots
    for key in _response_keys(
        intent_type, slots, first.domain if first is not None else None
    ):
        text = answers.get(key)
        if text is None:
            continue
        # German says "{{ slots.name }} eingeschaltet". Rendered for a room, with no
        # name to put there, that is a sentence that starts with a blank.
        if any(name not in speech_slots for name in _SLOT_REFERENCE.findall(text)):
            continue
        try:
            rendered = template.Template(text, hass).async_render(
                {
                    "slots": speech_slots,
                    "state": template.TemplateState(hass, first) if first else None,
                    "query": {
                        "matched": [
                            template.TemplateState(hass, state)
                            for state in response.matched_states
                        ],
                        "unmatched": [
                            template.TemplateState(hass, state)
                            for state in response.unmatched_states
                        ],
                    },
                },
                parse_result=False,
            )
        except TemplateError as err:
            _LOGGER.debug(
                "the %s answer for %s did not render: %s", key, intent_type, err
            )
            continue
        if sentence := " ".join(str(rendered).split()):
            return sentence
    return None


async def _speak_the_answer(
    hass: HomeAssistant,
    response: ha_intent.IntentResponse,
    language: str,
    say: dict[str, str],
) -> None:
    """Say what a state question found, in the language the pipeline is speaking.

    `HassGetState` fills in the matched states and stops. The spoken sentence is
    normally written by the default agent's response templates, which run only for
    sentences the default agent itself matched, so routing the intent here leaves a
    correct answer nobody hears. This renders the same template the default agent
    would have used. An English sentence is the last resort, for a language the
    intents package does not carry.
    """
    if response.response_type is not ha_intent.IntentResponseType.QUERY_ANSWER:
        return
    if response.speech:
        return
    matched = response.matched_states
    if not matched:
        response.async_set_speech(say["query_not_found"])
        return
    spoken = await _render_state_answer(
        hass, list(matched), list(response.unmatched_states), language
    )
    if spoken is None:
        spoken = ", ".join(f"{state.name} is {state.state}" for state in matched) + "."
    response.async_set_speech(spoken)
