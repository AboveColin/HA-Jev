"""Diagnostics. Users paste these into issues, so the key never appears."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from . import JevConfigEntry

# async_redact_data matches keys exactly, so every spelling that can appear is listed.
TO_REDACT = {CONF_API_KEY, "api_key", "apikey", "authorization", "key"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: JevConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data
    usage = runtime.usage
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "model_version": runtime.model_version,
        "usage_today": {
            "day": usage.day.isoformat(),
            "calls": usage.calls,
            "input_tokens": usage.input_tokens,
            "budget": usage.budget,
            "budget_exceeded": usage.budget_exceeded,
            "estimated_cost_usd": round(usage.estimated_cost, 6),
        },
        # The sentences the conversation agent routed, and what it made of each
        # one. These are what was actually said to the house, so read a diagnostics
        # file before pasting it into a public issue.
        "conversation_traces": list(runtime.conversation_traces),
        "contexts": [
            {
                "name": coordinator.context_config.name,
                "scan_interval_seconds": coordinator.context_config.scan_interval,
                "trigger_entities": coordinator.context_config.trigger_entities,
                "last_update_success": coordinator.last_update_success,
                "last_latency_ms": coordinator.last_latency_ms,
                # The rendered state is the first thing to look at when an answer
                # surprises someone, so it belongs here rather than in an attribute.
                "last_evaluated_state": coordinator.last_state_text,
                "questions": [
                    {
                        "key": question.key,
                        "name": question.name,
                        "type": question.kind,
                        "threshold": question.threshold,
                    }
                    for question in coordinator.context_config.questions
                ],
            }
            for coordinator in runtime.coordinators.values()
        ],
    }
