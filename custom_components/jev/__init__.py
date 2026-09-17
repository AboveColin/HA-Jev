"""Ask Jev typed questions about the state of your house.

One context is one API call. Every question attached to a context is evaluated in
isolation against the same rendered state, so questions batch almost for free in
time. They are not free in money: question text is billed as input tokens.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_NAME, CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import slugify
from jevclient import USD_PER_MILLION_INPUT_TOKENS, JevClient

from .const import (
    CONF_BACKGROUND,
    CONF_CRITERIA,
    CONF_DAILY_TOKEN_BUDGET,
    CONF_ENTITIES,
    CONF_FALSE,
    CONF_INCLUDE_ATTRIBUTES,
    CONF_INSTRUCTIONS,
    CONF_PRICE_PER_MILLION,
    CONF_QUESTIONS,
    CONF_STATE_TEMPLATE,
    CONF_THRESHOLD,
    CONF_TRIGGER_ENTITIES,
    CONF_TRUE,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    MIN_UPDATE_INTERVAL_SECONDS,
    STORAGE_VERSION,
    TYPE_CHOICE,
    TYPE_NOUL,
    TYPE_SCORE,
)
from .coordinator import JevCoordinator, JevRuntimeData, UsageAccount
from .models import ContextConfig, build_question_config
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]

type JevConfigEntry = ConfigEntry[JevRuntimeData]


def _check_question_shape(raw: dict[str, Any]) -> dict[str, Any]:
    """Reject a question the API would reject, and say which field is wrong.

    The round trip would cost a request and return a 422 naming a field the user
    never wrote, so the check belongs here.
    """
    kind = raw["type"]
    criteria = raw.get(CONF_CRITERIA)
    name = raw.get(CONF_NAME, "?")
    if kind == TYPE_NOUL:
        if criteria is not None:
            raise vol.Invalid(
                f"question {name!r}: a noul takes 'true:' and 'false:' descriptions, "
                f"not 'criteria:'"
            )
    elif kind == TYPE_CHOICE:
        if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 255:
            raise vol.Invalid(
                f"question {name!r}: a choice needs 'criteria:' as a mapping of 2 to "
                f"255 options to a description (or to nothing), got "
                f"{len(criteria) if isinstance(criteria, dict) else 'none'}"
            )
    elif kind == TYPE_SCORE:
        if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
            raise vol.Invalid(
                f"question {name!r}: a score needs 'criteria:' as an ordered list of "
                f"2 to 10 levels, lowest first, got "
                f"{len(criteria) if isinstance(criteria, list) else 'none'}"
            )
    if kind != TYPE_NOUL and (raw.get(CONF_TRUE) or raw.get(CONF_FALSE)):
        raise vol.Invalid(f"question {name!r}: 'true:' and 'false:' apply to a noul only")
    if kind != TYPE_NOUL and raw.get(CONF_THRESHOLD) is not None:
        raise vol.Invalid(
            f"question {name!r}: 'threshold:' makes a binary sensor out of a noul, "
            f"and applies to a noul only"
        )
    return raw


QUESTION_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(CONF_NAME): cv.string,
            vol.Required("type"): vol.In([TYPE_NOUL, TYPE_CHOICE, TYPE_SCORE]),
            vol.Required(CONF_INSTRUCTIONS): cv.string,
            vol.Optional(CONF_TRUE): cv.string,
            vol.Optional(CONF_FALSE): cv.string,
            vol.Optional(CONF_CRITERIA): vol.Any(
                {cv.string: vol.Any(cv.string, None)}, [cv.string]
            ),
            vol.Optional(CONF_THRESHOLD): vol.All(
                vol.Coerce(float), vol.Range(min=0.0, max=1.0)
            ),
            vol.Optional(CONF_BACKGROUND): vol.Any(cv.string, dict, list),
        }
    ),
    _check_question_shape,
)

# A list of entity ids is the short form of the full picker, which is what almost
# everyone wants to write.
TARGET_SCHEMA = vol.Schema(
    {
        vol.Optional("entity_id"): cv.entity_ids,
        vol.Optional("device_id"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("area_id"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("floor_id"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("label_id"): vol.All(cv.ensure_list, [cv.string]),
    }
)


def _entities_to_selector(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        validated: dict[str, Any] = TARGET_SCHEMA(value)
        return validated
    return {"entity_id": cv.entity_ids(value)}


def _check_context_has_input(raw: dict[str, Any]) -> dict[str, Any]:
    if not raw.get(CONF_STATE_TEMPLATE) and not raw.get(CONF_ENTITIES):
        raise vol.Invalid(
            f"context {raw.get(CONF_NAME, '?')!r}: give it 'entities:' to look at, "
            f"'state:' to write the text yourself, or both"
        )
    return raw


CONTEXT_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(CONF_NAME): cv.string,
            vol.Optional(CONF_STATE_TEMPLATE): cv.template,
            vol.Optional(CONF_ENTITIES): _entities_to_selector,
            vol.Optional(CONF_INCLUDE_ATTRIBUTES, default=False): cv.boolean,
            vol.Optional(
                CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL_SECONDS
            ): vol.All(vol.Coerce(int), vol.Range(min=MIN_UPDATE_INTERVAL_SECONDS)),
            vol.Optional(CONF_TRIGGER_ENTITIES, default=[]): cv.entity_ids,
            vol.Required(CONF_QUESTIONS): vol.All(
                cv.ensure_list, [QUESTION_SCHEMA], vol.Length(min=1)
            ),
        }
    ),
    _check_context_has_input,
)

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.All(cv.ensure_list, [CONTEXT_SCHEMA])}, extra=vol.ALLOW_EXTRA
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Read the YAML contexts. The API key itself comes from the config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["yaml"] = config.get(DOMAIN, [])
    async_register_services(hass)
    return True


def _build_contexts(hass: HomeAssistant) -> list[ContextConfig]:
    contexts: list[ContextConfig] = []
    for raw in hass.data[DOMAIN].get("yaml", []):
        context_key = slugify(raw[CONF_NAME])
        questions = [
            build_question_config(q, f"{context_key}_{slugify(q[CONF_NAME])}")
            for q in raw[CONF_QUESTIONS]
        ]
        template = raw.get(CONF_STATE_TEMPLATE)
        if template is not None:
            template.hass = hass
        contexts.append(
            ContextConfig(
                key=context_key,
                name=raw[CONF_NAME],
                template=template,
                selector=raw.get(CONF_ENTITIES),
                include_attributes=raw[CONF_INCLUDE_ATTRIBUTES],
                questions=questions,
                scan_interval=raw[CONF_SCAN_INTERVAL],
                trigger_entities=raw[CONF_TRIGGER_ENTITIES],
            )
        )
    return contexts


async def async_setup_entry(hass: HomeAssistant, entry: JevConfigEntry) -> bool:
    """Set up one API key, its usage account and a coordinator per context."""
    client = JevClient(
        entry.data[CONF_API_KEY],
        session=async_get_clientsession(hass),
    )
    store: Store[dict[str, Any]] = Store(
        hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.usage"
    )
    usage = UsageAccount(
        day=date.today(),
        budget=entry.options.get(CONF_DAILY_TOKEN_BUDGET, 0),
        price_per_million=entry.options.get(
            CONF_PRICE_PER_MILLION, USD_PER_MILLION_INPUT_TOKENS
        ),
        store=store,
    )
    usage.restore(await store.async_load())
    runtime = JevRuntimeData(client=client, usage=usage)
    entry.runtime_data = runtime

    for context in _build_contexts(hass):
        coordinator = JevCoordinator(hass, entry, runtime, context)
        runtime.coordinators[context.key] = coordinator
        # Deliberately not async_config_entry_first_refresh: that aborts setup when
        # the first evaluation fails, and the two ways it fails are a spent budget
        # and an unreachable API. Both are states the user needs to see explained,
        # and the budget and usage entities that explain them only exist once setup
        # finishes. Answers stay unavailable instead.
        await coordinator.async_refresh()
        await coordinator.async_setup_triggers()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: JevConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: JevConfigEntry) -> bool:
    """Unload platforms and stop every trigger listener this entry created."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        for coordinator in entry.runtime_data.coordinators.values():
            coordinator.async_shutdown_triggers()
    return unloaded
