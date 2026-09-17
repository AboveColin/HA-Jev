"""The actions.

One action per question type, because a mapping of question objects is a lot to ask
of someone writing their first automation, and three quarters of the uses only ever
need one question. `jev.ask` stays for the case the other three cannot express:
several questions about the same state, answered in one request.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import voluptuous as vol
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
from jevclient import (
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

from .const import (
    ATTR_ANSWERS,
    ATTR_CONFIG_ENTRY,
    ATTR_LATENCY_MS,
    ATTR_QUESTIONS,
    ATTR_USAGE,
    CONF_FALSE_MEANS,
    CONF_INSTRUCTIONS,
    CONF_LEVELS,
    CONF_OPTION_DESCRIPTIONS,
    CONF_OPTIONS,
    CONF_STATE_TEMPLATE,
    CONF_THRESHOLD,
    CONF_TRUE_MEANS,
    DOMAIN,
    SERVICE_ASK,
    SERVICE_CHOICE,
    SERVICE_NOUL,
    SERVICE_SCORE,
    TYPE_CHOICE,
    TYPE_NOUL,
    TYPE_SCORE,
)

# instructions and criteria values accept a string, an object or an array.
ENTRY = vol.Any(cv.string, dict, list)

_BASE = {
    vol.Required(CONF_STATE_TEMPLATE): vol.Any(cv.string, dict, list),
    vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
    vol.Required(CONF_INSTRUCTIONS): ENTRY,
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
        vol.Required(CONF_STATE_TEMPLATE): vol.Any(cv.string, dict, list),
        vol.Required(ATTR_QUESTIONS): vol.Schema({cv.string: dict}),
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
        raise ServiceValidationError(f"the state template failed: {err}") from err


def _entry(hass: HomeAssistant, call: ServiceCall) -> Any:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if wanted := call.data.get(ATTR_CONFIG_ENTRY):
        entries = [e for e in entries if e.entry_id == wanted]
    if not entries:
        raise ServiceValidationError(
            "no loaded Jev config entry to ask with. Add the integration first, or "
            "pick one under API key when more than one is set up."
        )
    return entries[0]


async def _ask(hass: HomeAssistant, call: ServiceCall, questions: dict[str, Question]):
    """Send one request and account for what it cost."""
    entry = _entry(hass, call)
    state = _render(hass, call.data[CONF_STATE_TEMPLATE])
    try:
        response = await entry.runtime_data.client.ask(state, questions)
    except JevAuthError as err:
        raise HomeAssistantError(f"TypeSafe rejected the API key: {err}") from err
    except JevError as err:
        raise HomeAssistantError(f"asking Jev failed: {err}") from err
    usage = entry.runtime_data.usage
    usage.roll_over(date.today())
    usage.record(response.usage.input_tokens)
    entry.runtime_data.model_version = response.model or entry.runtime_data.model_version
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
    raise HomeAssistantError(f"unreadable answer of type {type(answer).__name__}")


def async_register_services(hass: HomeAssistant) -> None:
    """Register all four actions once, the first time the component loads."""
    if hass.services.has_service(DOMAIN, SERVICE_NOUL):
        return

    async def _noul(call: ServiceCall) -> ServiceResponse:
        question = Noul(
            call.data[CONF_INSTRUCTIONS],
            true=call.data.get(CONF_TRUE_MEANS),
            false=call.data.get(CONF_FALSE_MEANS),
        )
        response = await _ask(hass, call, {"answer": question})
        answer = response.answers["answer"]
        assert isinstance(answer, NoulAnswer)
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
            option: descriptions.get(option) for option in call.data[CONF_OPTIONS]
        }
        try:
            question = Choice(call.data[CONF_INSTRUCTIONS], criteria)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        response = await _ask(hass, call, {"answer": question})
        answer = response.answers["answer"]
        assert isinstance(answer, ChoiceAnswer)
        return {
            "choice": answer.choice,
            "confidence": answer.confidence,
            "probabilities": answer.probabilities,
            **_envelope(response),
        }

    async def _score(call: ServiceCall) -> ServiceResponse:
        try:
            question = Score(call.data[CONF_INSTRUCTIONS], call.data[CONF_LEVELS])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        response = await _ask(hass, call, {"answer": question})
        answer = response.answers["answer"]
        assert isinstance(answer, ScoreAnswer)
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
        from .models import build_question

        questions: dict[str, Question] = {}
        for key, raw in call.data[ATTR_QUESTIONS].items():
            if "type" not in raw or CONF_INSTRUCTIONS not in raw:
                raise ServiceValidationError(
                    f"question {key!r} needs both 'type' and 'instructions'. Types are "
                    f"'{TYPE_NOUL}', '{TYPE_CHOICE}' and '{TYPE_SCORE}'."
                )
            try:
                questions[key] = build_question(raw)
            except (KeyError, ValueError) as err:
                raise ServiceValidationError(
                    f"question {key!r} is not valid: {err}"
                ) from err
        response = await _ask(hass, call, questions)
        return {
            ATTR_ANSWERS: {k: answer_as_dict(v) for k, v in response.answers.items()},
            **_envelope(response),
        }

    for name, handler, schema in (
        (SERVICE_NOUL, _noul, NOUL_SCHEMA),
        (SERVICE_CHOICE, _choice, CHOICE_SCHEMA),
        (SERVICE_SCORE, _score, SCORE_SCHEMA),
        (SERVICE_ASK, _ask_many, ASK_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN, name, handler, schema=schema, supports_response=SupportsResponse.ONLY
        )
