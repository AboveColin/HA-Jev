"""The actions.

One action per question type, because a mapping of question objects is a lot to ask
of someone writing their first automation, and three quarters of the uses only ever
need one question. `jev.ask` stays for the case the other three cannot express:
several questions about the same state, answered in one request.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    TemplateError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util
from jevclient import (
    MAX_CHOICE_OPTIONS,
    MAX_SCORE_LEVELS,
    MIN_CHOICE_OPTIONS,
    MIN_SCORE_LEVELS,
    Box,
    BoxAnswer,
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

if TYPE_CHECKING:
    from . import JevConfigEntry

from .calibrate import CALIBRATE_SCHEMA, async_calibrate
from .const import (
    ATTR_ANSWERS,
    ATTR_CONFIG_ENTRY,
    ATTR_LATENCY_MS,
    ATTR_QUESTIONS,
    ATTR_USAGE,
    ATTR_USE_JEV,
    CONF_BACKGROUND,
    CONF_FALSE_MEANS,
    CONF_IMAGE,
    CONF_IMAGES,
    CONF_INCLUDE_ATTRIBUTES,
    CONF_INSTRUCTIONS,
    CONF_LEVELS,
    CONF_MODEL,
    CONF_OPTION_DESCRIPTIONS,
    CONF_OPTIONS,
    CONF_STATE_TEMPLATE,
    CONF_THRESHOLD,
    CONF_TRUE_MEANS,
    DOMAIN,
    SERVICE_ASK,
    SERVICE_BOX,
    SERVICE_CALIBRATE,
    SERVICE_CHOICE,
    SERVICE_HOUSE_CHECK,
    SERVICE_NOUL,
    SERVICE_SCORE,
    SERVICE_UNDO_HOUSE_CHECK,
    TYPE_BOX,
    TYPE_CHOICE,
    TYPE_NOUL,
    TYPE_SCORE,
)
from .images import (
    RequestImages,
    async_entity_image,
    async_entity_images,
    entity_label,
    no_images,
    with_images,
)
from .models import ENTRY, build_question, compose_instructions
from .payload import payload_bytes
from .statebuilder import async_build_state

_LOGGER = logging.getLogger(__name__)

# What the target picker puts in call.data, which must not reach the question body.
TARGET_KEYS = ("entity_id", "device_id", "area_id", "floor_id", "label_id")

_BASE = {
    vol.Optional(CONF_STATE_TEMPLATE): vol.Any(cv.string, dict, list),
    vol.Optional(CONF_IMAGES, default=list): cv.entity_ids,
    vol.Optional(CONF_BACKGROUND): ENTRY,
    vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
    vol.Optional(CONF_MODEL): cv.string,
    vol.Optional(CONF_INCLUDE_ATTRIBUTES, default=False): cv.boolean,
    vol.Required(CONF_INSTRUCTIONS): ENTRY,
    **cv.TARGET_SERVICE_FIELDS,
}

NOUL_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Optional(CONF_TRUE_MEANS): ENTRY,
        vol.Optional(CONF_FALSE_MEANS): ENTRY,
        vol.Optional(CONF_THRESHOLD, default=0.5): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=1.0)
        ),
    }
)

CHOICE_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Required(CONF_OPTIONS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_OPTION_DESCRIPTIONS, default={}): dict,
    }
)

SCORE_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Required(CONF_LEVELS): vol.All(cv.ensure_list, [ENTRY]),
    }
)

ASK_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_STATE_TEMPLATE): vol.Any(cv.string, dict, list),
        vol.Optional(CONF_IMAGES, default=list): cv.entity_ids,
        # An empty mapping is still a billed request, answered with nothing.
        vol.Required(ATTR_QUESTIONS): vol.All(
            vol.Schema({cv.string: dict}), vol.Length(min=1)
        ),
        vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
        vol.Optional(CONF_MODEL): cv.string,
        vol.Optional(CONF_INCLUDE_ATTRIBUTES, default=False): cv.boolean,
        **cv.TARGET_SERVICE_FIELDS,
    }
)

# One picture, so the box is unambiguous: its fractions are of that picture.
BOX_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_IMAGE): cv.entity_id,
        vol.Required(CONF_INSTRUCTIONS): ENTRY,
        vol.Optional(CONF_BACKGROUND): ENTRY,
        vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
        vol.Optional(CONF_MODEL): cv.string,
    }
)

HOUSE_CHECK_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_USE_JEV, default=True): cv.boolean,
        vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
    }
)

UNDO_HOUSE_CHECK_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
    }
)


def _render(hass: HomeAssistant, value: Any) -> Any:
    """Render a template that reached us unrendered.

    A script renders action data before we see it, so this only fires for a call
    made straight from the developer tools or the REST API, where nothing else
    would. Anything already rendered no longer contains the markers.
    """
    if not isinstance(value, str) or ("{{" not in value and "{%" not in value):
        return value
    try:
        return Template(value, hass).async_render(parse_result=False)
    except TemplateError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="template_failed",
            translation_placeholders={"reason": str(err)},
        ) from err


def _typed(answer: Any, expected: type, question_type: str) -> Any:
    """The API is schema-guaranteed, so this only fires if that guarantee breaks.

    A bare assert would say nothing about what arrived, and disappears entirely
    under python -O.
    """
    if not isinstance(answer, expected):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="wrong_answer_type",
            translation_placeholders={
                "question_type": question_type,
                "got": type(answer).__name__,
            },
        )
    return answer


def _instructions(call: ServiceCall) -> Any:
    return compose_instructions(
        call.data[CONF_INSTRUCTIONS], call.data.get(CONF_BACKGROUND)
    )


def _entry(hass: HomeAssistant, call: ServiceCall) -> JevConfigEntry:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if wanted := call.data.get(ATTR_CONFIG_ENTRY):
        entries = [e for e in entries if e.entry_id == wanted]
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_entry"
        )
    # Each entry has its own key and budget, so taking the first would bill
    # whichever one happened to load first.
    if len(entries) > 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_ambiguous",
            translation_placeholders={
                "entries": ", ".join(sorted(e.title for e in entries))
            },
        )
    return entries[0]


async def _ask(
    hass: HomeAssistant, call: ServiceCall, questions: dict[str, Question]
) -> JevResponse:
    """Send one request and account for what it cost."""
    entry = _entry(hass, call)
    selector = {
        key: value for key, value in call.data.items() if key in TARGET_KEYS and value
    }
    image_ids = call.data[CONF_IMAGES]
    if not selector and not call.data.get(CONF_STATE_TEMPLATE) and not image_ids:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="nothing_to_judge"
        )
    images = await async_entity_images(hass, image_ids)
    state = with_images(
        async_build_state(
            hass,
            _render(hass, call.data.get(CONF_STATE_TEMPLATE)),
            selector,
            call.data[CONF_INCLUDE_ATTRIBUTES],
        ),
        images,
    )
    _LOGGER.debug(
        "asking %s question(s) about a %s state with %s image(s): %s",
        len(questions),
        type(state).__name__,
        len(images.assets),
        state,
    )
    return await async_ask(
        hass, entry, state, questions, images, call.data.get(CONF_MODEL)
    )


async def async_ask(
    hass: HomeAssistant,
    entry: JevConfigEntry,
    state: Any,
    questions: dict[str, Question],
    images: RequestImages | None = None,
    model: str | None = None,
) -> JevResponse:
    """Send one request inside the daily budget, and account for what it cost.

    `model` overrides the entry's model for this request only. A Box question needs
    Jev 1.14, and the entry usually asks jev-latest.
    """
    images = images or no_images()
    usage = entry.runtime_data.usage
    usage.roll_over(dt_util.now().date())
    request_bytes = payload_bytes(state, questions, model or entry.runtime_data.model)
    # The same check a context and the voice agent make: a script that loops on an
    # action is the runaway the budget exists for.
    estimate = usage.estimate_tokens(request_bytes, images.tokens)
    if usage.would_exceed_with(estimate):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="action_over_budget",
            translation_placeholders={
                "estimate": str(estimate),
                "remaining": str(usage.remaining()),
                "budget": str(usage.budget),
            },
        )
    try:
        with usage.reservation(estimate):
            response = await entry.runtime_data.client.ask(
                state, questions, model=model, assets=images.assets or None
            )
    except JevAuthError as err:
        entry.async_start_reauth(hass)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="auth_rejected",
            translation_placeholders={"reason": str(err)},
        ) from err
    except JevError as err:
        # jev-1.13.0 answers a Box with a bare 400 "Invalid request.", which says
        # nothing about why (measured 2026-10-16).
        if err.status == 400 and any(isinstance(q, Box) for q in questions.values()):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="box_refused",
                # The text puts a full stop after the reason, and the API's
                # message has one of its own.
                translation_placeholders={
                    "model": model or entry.runtime_data.model,
                    "reason": str(err).rstrip("."),
                },
            ) from err
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="ask_failed",
            translation_placeholders={"reason": str(err)},
        ) from err
    usage.record(response.usage.input_tokens, request_bytes, images.tokens)
    # The version sensor reports the entry's model, not a one-off override.
    if model is None:
        entry.runtime_data.model_version = (
            response.model or entry.runtime_data.model_version
        )
    usage.notify()
    return response


def _envelope(response: JevResponse) -> dict[str, Any]:
    return {
        "model": response.model,
        ATTR_LATENCY_MS: round(response.latency_ms, 1),
        ATTR_USAGE: {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
    }


def _edges(answer: BoxAnswer) -> dict[str, Any]:
    """Fractions of the picture's width and height, measured from its top left."""
    return {
        "left": answer.left,
        "top": answer.top,
        "right": answer.right,
        "bottom": answer.bottom,
        "confidence": answer.confidence,
    }


def answer_as_dict(answer: Any) -> dict[str, Any]:
    """Flatten one answer so a template can read it without knowing the classes."""
    if isinstance(answer, NoulAnswer):
        return {"type": TYPE_NOUL, "noul": answer.noul}
    if isinstance(answer, ChoiceAnswer):
        return {
            "type": TYPE_CHOICE,
            "choice": answer.choice,
            "probabilities": answer.probabilities,
            "confidence": answer.confidence,
        }
    if isinstance(answer, ScoreAnswer):
        return {
            "type": TYPE_SCORE,
            "score": answer.score,
            "normalized": round(answer.normalized, 4),
            "nearest_level": answer.nearest_level,
            "legend": answer.legend,
            "probabilities": answer.probabilities,
            "confidence": answer.confidence,
        }
    if isinstance(answer, BoxAnswer):
        return {"type": TYPE_BOX, **_edges(answer)}
    # Reached only if the client grows a fifth answer type, which a user meets as
    # a library upgrade rather than as a bug in their configuration.
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="unreadable_answer",
        translation_placeholders={"kind": type(answer).__name__},
    )


def async_register_services(hass: HomeAssistant) -> None:
    """Register every action once, the first time the component loads."""
    if hass.services.has_service(DOMAIN, SERVICE_NOUL):
        return

    async def _noul(call: ServiceCall) -> ServiceResponse:
        # A blueprint passes "" for a meaning its user left blank, and the client
        # would send that as an empty criterion. Blank is the same as absent.
        question = Noul(
            _instructions(call),
            true=call.data.get(CONF_TRUE_MEANS) or None,
            false=call.data.get(CONF_FALSE_MEANS) or None,
        )
        response = await _ask(hass, call, {"answer": question})
        answer = _typed(response.answers["answer"], NoulAnswer, TYPE_NOUL)
        threshold = call.data[CONF_THRESHOLD]
        return {
            "noul": answer.noul,
            # The docs are explicit that a value near 0.5 means the model cannot
            # tell, not that the answer is halfway true, so the boolean is the
            # caller's threshold and nothing more.
            "is_true": answer.noul >= threshold,
            "threshold": threshold,
            **_envelope(response),
        }

    async def _choice(call: ServiceCall) -> ServiceResponse:
        descriptions = call.data[CONF_OPTION_DESCRIPTIONS]
        criteria = {
            option: descriptions.get(option) or None for option in call.data[CONF_OPTIONS]
        }
        try:
            question = Choice(_instructions(call), criteria)
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="choice_options_out_of_range",
                translation_placeholders={
                    "min": str(MIN_CHOICE_OPTIONS),
                    "max": str(MAX_CHOICE_OPTIONS),
                    "count": str(len(criteria)),
                },
            ) from err
        response = await _ask(hass, call, {"answer": question})
        answer = _typed(response.answers["answer"], ChoiceAnswer, TYPE_CHOICE)
        return {
            "choice": answer.choice,
            "confidence": answer.confidence,
            "probabilities": answer.probabilities,
            **_envelope(response),
        }

    async def _score(call: ServiceCall) -> ServiceResponse:
        try:
            question = Score(_instructions(call), call.data[CONF_LEVELS])
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="score_levels_out_of_range",
                translation_placeholders={
                    "min": str(MIN_SCORE_LEVELS),
                    "max": str(MAX_SCORE_LEVELS),
                    "count": str(len(call.data[CONF_LEVELS])),
                },
            ) from err
        response = await _ask(hass, call, {"answer": question})
        answer = _typed(response.answers["answer"], ScoreAnswer, TYPE_SCORE)
        return {
            "score": answer.score,
            # Two rubrics of different lengths are not comparable until each is
            # divided by its own top level, which is what a weighted composite needs.
            "normalized": round(answer.normalized, 4),
            "nearest_level": answer.nearest_level,
            "confidence": answer.confidence,
            "legend": answer.legend,
            "probabilities": answer.probabilities,
            **_envelope(response),
        }

    async def _ask_many(call: ServiceCall) -> ServiceResponse:
        questions: dict[str, Question] = {}
        for key, raw in call.data[ATTR_QUESTIONS].items():
            if "type" not in raw or CONF_INSTRUCTIONS not in raw:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="question_shape",
                    translation_placeholders={"key": key},
                )
            try:
                questions[key] = build_question(raw)
            except (KeyError, ValueError) as err:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="question_invalid",
                    translation_placeholders={"key": key, "reason": str(err)},
                ) from err
        boxes = [k for k, q in questions.items() if isinstance(q, Box)]
        if boxes and not call.data[CONF_IMAGES]:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="box_needs_image",
                translation_placeholders={"key": boxes[0]},
            )
        response = await _ask(hass, call, questions)
        return {
            ATTR_ANSWERS: {k: answer_as_dict(v) for k, v in response.answers.items()},
            **_envelope(response),
        }

    async def _box(call: ServiceCall) -> ServiceResponse:
        entry = _entry(hass, call)
        entity_id = call.data[CONF_IMAGE]
        prepared = await async_entity_image(hass, entity_id)
        images = no_images()
        images.add(entity_label(hass, entity_id), prepared)
        response = await async_ask(
            hass,
            entry,
            with_images(None, images),
            {"answer": Box(_instructions(call))},
            images,
            call.data.get(CONF_MODEL),
        )
        answer = _typed(response.answers["answer"], BoxAnswer, TYPE_BOX)
        return {
            **_edges(answer),
            # Upright, as the camera sent it. The edges are fractions of this, so
            # an edge in pixels is the fraction times the side.
            "width": prepared.source_width,
            "height": prepared.source_height,
            **_envelope(response),
        }

    async def _calibrate(call: ServiceCall) -> ServiceResponse:
        return await async_calibrate(hass, call)

    async def _house_check(call: ServiceCall) -> ServiceResponse:
        check = _entry(hass, call).runtime_data.house_check
        findings = await check.async_run(call.data[ATTR_USE_JEV])
        return {"findings": [f.as_dict() for f in findings]}

    async def _undo_house_check(call: ServiceCall) -> ServiceResponse:
        check = _entry(hass, call).runtime_data.house_check
        restored: list[Any] = await check.async_undo(
            call.data.get(ATTR_ENTITY_ID), call.context
        )
        return {"restored": restored}

    for name, handler, schema in (
        (SERVICE_NOUL, _noul, NOUL_SCHEMA),
        (SERVICE_CHOICE, _choice, CHOICE_SCHEMA),
        (SERVICE_SCORE, _score, SCORE_SCHEMA),
        (SERVICE_ASK, _ask_many, ASK_SCHEMA),
        (SERVICE_BOX, _box, BOX_SCHEMA),
        (SERVICE_CALIBRATE, _calibrate, CALIBRATE_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN, name, handler, schema=schema, supports_response=SupportsResponse.ONLY
        )
    # These two change the house as well as answering, so an automation can call
    # them without asking for the response.
    for name, handler, schema in (
        (SERVICE_HOUSE_CHECK, _house_check, HOUSE_CHECK_SCHEMA),
        (SERVICE_UNDO_HOUSE_CHECK, _undo_house_check, UNDO_HOUSE_CHECK_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
