"""The config flow, which is the one thing every user touches."""

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.data_entry_flow import FlowResultType
from jevclient import JevAuthError, JevConnectionError

from custom_components.jev.const import (
    CONF_DAILY_TOKEN_BUDGET,
    CONF_PRICE_PER_MILLION,
    DOMAIN,
)

from .conftest import API_KEY


async def test_user_flow_creates_entry(hass, mock_client):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Jev"
    assert result["data"] == {CONF_API_KEY: API_KEY}
    # Setup asks one short question to prove the key works.
    assert mock_client.ask.await_count == 1


@pytest.mark.parametrize(
    ("error", "expected"),
    [(JevAuthError("no"), "invalid_auth"), (JevConnectionError("no"), "cannot_connect")],
)
async def test_user_flow_errors_recover(hass, mock_client, error, expected):
    """A rejected key shows the reason and leaves the form usable."""
    mock_client.ask.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    mock_client.ask.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_same_key_twice_is_refused(hass, mock_client, config_entry):
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.jev.config_flow.hashlib.sha256"
    ) as sha:
        sha.return_value.hexdigest.return_value = config_entry.unique_id + "padding"
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: API_KEY}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_the_key_itself_is_never_the_unique_id(hass, mock_client):
    """A unique id lands in the registry, so it must not be the credential."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id != API_KEY
    assert API_KEY not in entry.unique_id
    assert len(entry.unique_id) == 16


async def test_reauth_replaces_the_key(hass, mock_client, config_entry):
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_client.ask.side_effect = JevAuthError("still no")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "still-wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.ask.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "a-working-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_API_KEY] == "a-working-key"


async def test_options_flow_stores_the_budget(hass, loaded_entry):
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_DAILY_TOKEN_BUDGET: 50_000, CONF_PRICE_PER_MILLION: 0.042},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert loaded_entry.options[CONF_DAILY_TOKEN_BUDGET] == 50_000
