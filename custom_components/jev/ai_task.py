"""An AI Task entity, so a script can ask a question and act on the answer at once.

A question subentry is evaluated on a schedule and lands in a sensor, which is the
right shape when you want the answer over time. It is the wrong shape inside a
script: the sensor can still hold the answer from before the change that started
the script, because a trigger wakes the context through a 5 second debounce. This
entity answers at the moment it is called.

Home Assistant allows a task with no `structure`, which asks for free text. Jev
returns typed answers and nothing else, so that call is refused here rather than
answered with something invented.

An image attachment, such as a camera snapshot, goes into the request beside the
instructions. Any other attachment is refused, because Jev reads text and pictures
and nothing else.

The same entity answers `ai_task.evaluate`, the decision task Home Assistant added in
2026.11. Its question types are Jev's own, noul, choice and score, so the questions go
out as written and the answers come back with their probabilities. That makes the
entity one of the choices for the Decision default under Settings > AI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from homeassistant.components import ai_task, conversation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util
from jevclient import (
    MAX_CHOICE_OPTIONS,
    MAX_SCORE_LEVELS,
    MIN_CHOICE_OPTIONS,
    MIN_SCORE_LEVELS,
    Answer,
    Choice,
    ChoiceAnswer,
    JevAuthError,
    JevError,
    JevResponse,
    Noul,
    NoulAnswer,
    Question,
    Score,
    ScoreAnswer,
)

from .const import DOMAIN
from .coordinator import JevRuntimeData
from .entity import build_device_info
from .images import RequestImages, no_images, prepare_image, with_images
from .payload import payload_bytes
from .structure import RESERVED_KEY, questions_from_structure, values_from_answers

# One request per task, and the entity holds no data of its own between calls.
PARALLEL_UPDATES = 0

if TYPE_CHECKING:
    from homeassistant.components.ai_task.evaluation import (
        EvaluationAnswer,
        EvaluationQuestion,
        EvaluationTask,
        EvaluationTaskResult,
    )
    from homeassistant.components.conversation import Attachment

    from . import JevConfigEntry

# Home Assistant 2026.11 added decision tasks. hacs.json still allows 2026.9, where
# the flag does not exist, and there the entity offers what it offered before.
_EVALUATE = getattr(ai_task.AITaskEntityFeature, "EVALUATE", 0)


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
    _attr_supported_features = (
        ai_task.AITaskEntityFeature.GENERATE_DATA
        | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
        | _EVALUATE
    )

    def __init__(self, entry: JevConfigEntry) -> None:
        self._entry = entry
        self._runtime: JevRuntimeData = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_ai_task"
        self._attr_device_info = build_device_info(entry.entry_id, self._runtime)

    @override
    async def _async_generate_data(
        self, task: ai_task.GenDataTask, chat_log: conversation.ChatLog
    ) -> ai_task.GenDataTaskResult:
        if task.structure is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="task_needs_structure",
                translation_placeholders={"task": task.name},
            )
        questions = questions_from_structure(task.structure)
        images = await self._async_images(task.attachments)
        response = await self._async_ask(
            task.name, with_images(task.instructions, images), questions, images
        )
        data = values_from_answers(task.structure, response.answers)
        data[RESERVED_KEY] = _certainty(response)
        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id, data=data
        )

    # No @override: Home Assistant before 2026.11 has no such method to override, and
    # the type check runs against the oldest release hacs.json allows.
    async def _async_evaluate(self, task: EvaluationTask) -> EvaluationTaskResult:
        """Answer a decision task. Home Assistant has validated the questions."""
        # Imported here because the module is new in 2026.11, and this method only
        # runs on a release that has it.
        from homeassistant.components.ai_task import evaluation

        questions = {
            key: _question(key, question) for key, question in task.questions.items()
        }
        images = await self._async_images(task.attachments)
        response = await self._async_ask(
            task.name, with_images(task.state, images), questions, images
        )
        return evaluation.EvaluationTaskResult(
            answers={
                key: _evaluation_answer(key, task.questions[key], response)
                for key in task.questions
            }
        )

    async def _async_ask(
        self,
        task_name: str,
        state: Any,
        questions: dict[str, Question],
        images: RequestImages,
    ) -> JevResponse:
        """One request, refused before it is sent if it would not fit the budget."""
        usage = self._runtime.usage
        usage.roll_over(dt_util.now().date())
        request_bytes = payload_bytes(state, questions, self._runtime.model)
        estimate = usage.estimate_tokens(request_bytes, images.tokens)
        if usage.would_exceed_with(estimate):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="task_over_budget",
                translation_placeholders={
                    "task": task_name,
                    "estimate": str(estimate),
                    "remaining": str(usage.remaining()),
                    "budget": str(usage.budget),
                },
            )

        try:
            with usage.reservation(estimate):
                response = await self._runtime.client.ask(
                    state, questions, assets=images.assets or None
                )
        except JevAuthError as err:
            self._entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="auth_rejected",
                translation_placeholders={"reason": str(err)},
            ) from err
        except JevError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="ask_failed",
                translation_placeholders={"reason": str(err)},
            ) from err

        usage.record(response.usage.input_tokens, request_bytes, images.tokens)
        self._runtime.model_version = response.model or self._runtime.model_version
        usage.notify()
        return response

    async def _async_images(self, attachments: list[Attachment] | None) -> RequestImages:
        """Every attachment, prepared. Home Assistant has already written each to disk."""
        images = no_images()
        for attachment in attachments or []:
            if not attachment.mime_type.startswith("image/"):
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="attachment_not_image",
                    translation_placeholders={
                        "attachment": attachment.media_content_id,
                        "mime_type": attachment.mime_type,
                    },
                )
            data = await self.hass.async_add_executor_job(attachment.path.read_bytes)
            prepared = await self.hass.async_add_executor_job(
                prepare_image, data, attachment.media_content_id
            )
            # media-source://camera/camera.front_door names the camera, which is
            # the label an instruction would use for it.
            name = attachment.media_content_id.rsplit("/", 1)[-1]
            images.add({"name": name}, prepared)
        return images


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


def _question(key: str, question: EvaluationQuestion) -> Question:
    """A decision question as Jev takes it.

    Home Assistant took the three types from Jev, so only the limits differ: it asks
    for at least two options or levels and sets no top, and Jev sets one.
    """
    if question["type"] == "noul":
        return Noul(instructions=question["instructions"])
    if question["type"] == "choice":
        options = question["criteria"]
        if len(options) > MAX_CHOICE_OPTIONS:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="field_options_out_of_range",
                translation_placeholders={
                    "field": key,
                    "count": str(len(options)),
                    "min": str(MIN_CHOICE_OPTIONS),
                    "max": str(MAX_CHOICE_OPTIONS),
                },
            )
        return Choice(instructions=question["instructions"], criteria=dict(options))
    levels = question["criteria"]
    if len(levels) > MAX_SCORE_LEVELS:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="question_levels_out_of_range",
            translation_placeholders={
                "question": key,
                "count": str(len(levels)),
                "min": str(MIN_SCORE_LEVELS),
                "max": str(MAX_SCORE_LEVELS),
            },
        )
    return Score(instructions=question["instructions"], criteria=list(levels))


def _evaluation_answer(
    key: str, question: EvaluationQuestion, response: JevResponse
) -> EvaluationAnswer:
    """Jev's answer in the shape Home Assistant returns.

    Jev leaves out an option or level it gives no weight, and Home Assistant wants one
    probability for each, so the gaps are zero. A score's levels come back keyed by
    their index as text, and Home Assistant wants them as a list in level order.
    """
    from homeassistant.components.ai_task import evaluation

    answer = response.answers.get(key)
    if question["type"] == "noul" and isinstance(answer, NoulAnswer):
        return evaluation.NoulAnswer(noul=answer.noul)
    if question["type"] == "choice" and isinstance(answer, ChoiceAnswer):
        return evaluation.ChoiceAnswer(
            choice=answer.choice,
            probabilities={
                option: answer.probabilities.get(option, 0.0)
                for option in question["criteria"]
            },
        )
    if question["type"] == "score" and isinstance(answer, ScoreAnswer):
        return evaluation.ScoreAnswer(
            score=answer.score,
            probabilities=[
                answer.probabilities.get(str(index), 0.0)
                for index in range(len(question["criteria"]))
            ],
        )
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="evaluation_answer_missing",
        translation_placeholders={"question": key, "type": question["type"]},
    )
