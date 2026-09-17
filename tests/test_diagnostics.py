"""Diagnostics get pasted into public issues, so the key must never be in them."""

from homeassistant.components.diagnostics import REDACTED
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.jev.const import DOMAIN

from .conftest import API_KEY
from .test_init import CONTEXT


async def test_the_key_is_redacted(hass, hass_client, mock_client, config_entry):
    hass.states.async_set("sensor.washer_power", "1.2")
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: [CONTEXT]})
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    data = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    assert data["entry"]["data"]["api_key"] == REDACTED
    assert API_KEY not in str(data)
    assert data["usage_today"]["input_tokens"] == 321
    assert data["contexts"][0]["name"] == "Laundry"
    # The rendered state is the first thing to look at when an answer surprises
    # someone, so it has to be in here.
    assert data["contexts"][0]["last_evaluated_state"]["entities"][0]["state"] == "1.2"


async def test_system_health_reports_reachability(hass, loaded_entry):
    assert await async_setup_component(hass, "system_health", {})
    await hass.async_block_till_done()
    from custom_components.jev.system_health import system_health_info

    info = await system_health_info(hass)
    assert "reachable" in info
