"""Config and options flow.

Setup makes one real request. It costs a few thousandths of a cent and it is the
only way to tell a working key from a typed one before entities appear.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from jevclient import (
    USD_PER_MILLION_INPUT_TOKENS,
    JevAuthError,
    JevClient,
    JevError,
    Noul,
)

from .const import (
    CONF_ALLOW_WHOLE_HOME,
    CONF_DAILY_TOKEN_BUDGET,
    CONF_FALLBACK_AGENT,
    CONF_MIN_CONFIDENCE,
    CONF_PRICE_PER_MILLION,
    DEFAULT_MIN_CONFIDENCE,
    DOMAIN,
    SUBENTRY_QUESTION,
)
from .subentry import JevQuestionSubentryFlow

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})


def _key_id(api_key: str) -> str:
    """The unique id for an entry holding this key.

    The key itself is never the unique id: it would land in the registry.
    """
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


class JevConfigFlow(ConfigFlow, domain=DOMAIN):
    """Take an API key and prove it works."""

    VERSION = 1

    async def _async_validate(self, api_key: str) -> str | None:
        """Return an error key, or None when the key answers."""
        client = JevClient(api_key, session=async_get_clientsession(self.hass))
        try:
            await client.ask("ok", {"probe": Noul("Is this text in English?")})
        except JevAuthError:
            return "invalid_auth"
        except JevError:
            return "cannot_connect"
        return None

    async def _async_swap_key(
        self, entry: ConfigEntry, user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        """Store a validated key on an existing entry, unique id and all.

        The unique id is the hash of the key, so a swap has to move it. Left
        where it was, it went on guarding the retired key and stopped guarding
        the one now in use: a second entry could then be added with the same key.

        _abort_if_unique_id_configured is not the guard here, because it counts
        this entry too and re-entering the same key is a legal no-op. Only
        another entry already holding the new key is a collision.
        """
        new_id = _key_id(user_input[CONF_API_KEY])
        for other in self._async_current_entries(include_ignore=True):
            if other.entry_id != entry.entry_id and other.unique_id == new_id:
                return self.async_abort(reason="already_configured")
        await self.async_set_unique_id(new_id)
        return self.async_update_reload_and_abort(
            entry, unique_id=new_id, data_updates=user_input
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            await self.async_set_unique_id(_key_id(api_key))
            self._abort_if_unique_id_configured()
            if error := await self._async_validate(api_key):
                errors["base"] = error
            else:
                return self.async_create_entry(title="Jev", data={CONF_API_KEY: api_key})
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if error := await self._async_validate(user_input[CONF_API_KEY]):
                errors["base"] = error
            else:
                return await self._async_swap_key(self._get_reauth_entry(), user_input)
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Swap the API key without removing the integration and losing its entities."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if error := await self._async_validate(user_input[CONF_API_KEY]):
                errors["base"] = error
            else:
                return await self._async_swap_key(
                    self._get_reconfigure_entry(), user_input
                )
        return self.async_show_form(
            step_id="reconfigure", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> JevOptionsFlow:
        return JevOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Questions are added one at a time, in the UI, without a YAML file."""
        return {SUBENTRY_QUESTION: JevQuestionSubentryFlow}


class JevOptionsFlow(OptionsFlow):
    """Spending limits and how the conversation agent should behave.

    The budget is a tripwire, not a quota to run against: put it past anything a
    working configuration would ever use, so only a runaway touches it.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_DAILY_TOKEN_BUDGET,
                    default=options.get(CONF_DAILY_TOKEN_BUDGET, 0),
                ): vol.All(vol.Coerce(int), vol.Range(min=0)),
                vol.Optional(
                    CONF_PRICE_PER_MILLION,
                    default=options.get(
                        CONF_PRICE_PER_MILLION, USD_PER_MILLION_INPUT_TOKENS
                    ),
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Optional(
                    CONF_FALLBACK_AGENT,
                    description={"suggested_value": options.get(CONF_FALLBACK_AGENT)},
                ): selector.ConversationAgentSelector(
                    selector.ConversationAgentSelectorConfig()
                ),
                vol.Optional(
                    CONF_MIN_CONFIDENCE,
                    default=options.get(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE),
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
                vol.Optional(
                    CONF_ALLOW_WHOLE_HOME,
                    default=options.get(CONF_ALLOW_WHOLE_HOME, False),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
