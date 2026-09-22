"""An AI Task entity, so a script can ask a question and act on the answer at once.

A question subentry is evaluated on a schedule and lands in a sensor, which is the
right shape when you want the answer over time. It is the wrong shape inside a
script: the sensor can still hold the answer from before the change that started
the script, because a trigger wakes the context through a 5 second debounce. This
entity answers at the moment it is called.

Home Assistant allows a task with no `structure`, which asks for free text. Jev
returns typed answers and nothing else, so that call is refused here rather than
answered with something invented.

Attachments need no handling: ai_task refuses them before the task reaches an
entity that does not declare SUPPORT_ATTACHMENTS.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, override

from homeassistant.components import ai_task, conversation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from jevclient import (
    Answer,
    ChoiceAnswer,
    JevError,
    JevResponse,
    NoulAnswer,
    ScoreAnswer,
)

from .coordinator import JevRuntimeData
from .entity import build_device_info
from .payload import payload_bytes
from .structure import RESERVED_KEY, questions_from_structure, values_from_answers

# One request per task, and the entity holds no data of its own between calls.
PARALLEL_UPDATES = 0

if TYPE_CHECKING:
    from . import JevConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JevConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([JevAITaskEntity(entry)])


class JevAITaskEntity(ai_task.AITaskEntity):
    """Answers a structured task against the state the caller writes.

    The configured contexts play no part here. A context is a subentry and this
    entity belongs to the config entry, so there is no context to pick: whatever
    the caller puts in `instructions` is the state Jev judges.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = ai_task.AITaskEntityFeature.GENERATE_DATA

    def __init__(self, entry: JevConfigEntry) -> None:
        self._runtime: JevRuntimeData = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_ai_task"
        self._attr_device_info = build_device_info(entry.entry_id, self._runtime)

    @override
    async def _async_generate_data(
        self, task: ai_task.GenDataTask, chat_log: conversation.ChatLog
    ) -> ai_task.GenDataTaskResult:
        if task.structure is None:
            raise ServiceValidationError(
                f"task {task.name!r} asks for free text, and Jev answers typed "
                f"questions only. Give the action a structure whose fields are "
                f"booleans, selects or numbers."
            )
        questions = questions_from_structure(task.structure)
        usage = self._runtime.usage
        usage.roll_over(date.today())
        request_bytes = payload_bytes(task.instructions, questions, self._runtime.model)
        estimate = usage.estimate_tokens(request_bytes)
        if usage.would_exceed_with(estimate):
            raise HomeAssistantError(
                f"task {task.name!r} costs about {estimate} input tokens and "
                f"{usage.remaining()} of the {usage.budget} daily budget are left. "
                f"Raise or clear the budget in the integration options to continue."
            )

        try:
            response = await self._runtime.client.ask(task.instructions, questions)
        except JevError as err:
            raise HomeAssistantError(f"TypeSafe did not answer: {err}") from err

        usage.record(response.usage.input_tokens, request_bytes)
        self._runtime.model_version = response.model or self._runtime.model_version
        usage.notify()

        data = values_from_answers(task.structure, response.answers)
        data[RESERVED_KEY] = _certainty(response)
        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id, data=data
        )


def _certainty(response: JevResponse) -> dict[str, Any]:
    """What the values do not carry.

    GenDataTaskResult holds a conversation id and the data, so a task that asks for
    a boolean gets back a bare true and never sees how sure Jev was. Confidence is
    the number that separates an answer worth acting on from a guess, so it travels
    beside the values rather than being dropped.
    """
    return {
        "model": response.model,
        "input_tokens": response.usage.input_tokens,
        "latency_ms": round(response.latency_ms),
        "answers": {
            key: _answer_detail(answer) for key, answer in response.answers.items()
        },
    }


def _answer_detail(answer: Answer) -> dict[str, Any]:
    if isinstance(answer, NoulAnswer):
        # A noul is a probability and carries no confidence of its own. How sure it
        # is is how far from a half it sits.
        return {"probability": round(answer.noul, 4)}
    if isinstance(answer, ChoiceAnswer):
        return {
            "confidence": round(answer.confidence, 3),
            "probabilities": {k: round(v, 4) for k, v in answer.probabilities.items()},
        }
    if isinstance(answer, ScoreAnswer):
        return {
            "confidence": round(answer.confidence, 3),
            "probabilities": {k: round(v, 4) for k, v in answer.probabilities.items()},
            "nearest_level": answer.nearest_level,
        }
    return {}
