"""The config flow, which is the one thing every user touches."""

import hashlib
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.data_entry_flow import FlowResultType
from jevclient import JevAuthError, JevConnectionError
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
    # Two short questions: the flow proves the key works before creating the entry,
    # then setup proves the service answers before any entity appears. Each is about
    # 40 input tokens.
    assert mock_client.ask.await_count == 2


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
    with patch("custom_components.jev.config_flow.hashlib.sha256") as sha:
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
    # A successful reauth reloads the entry, which creates the entities and writes
    # the registries. Without waiting, that lands during teardown, and if it lands
    # after shutdown has consumed the stores' one-shot final-write listeners their
    # timers survive and the harness fails the test on a lingering timer. It failed
    # that way on CI only, naming core.entity_registry rather than anything here.
    await hass.async_block_till_done()


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


async def test_reconfigure_swaps_the_key_and_keeps_the_entities(
    hass, mock_client, loaded_entry
):
    """Changing a key must not mean removing the integration and losing its history."""
    result = await loaded_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    mock_client.ask.side_effect = JevAuthError("that one is wrong too")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "still-wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.ask.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "a-fresh-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert loaded_entry.data[CONF_API_KEY] == "a-fresh-key"


def _key_id(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


async def test_a_swapped_key_takes_its_unique_id_with_it(hass, mock_client, config_entry):
    """The unique id is the hash of the key, so it has to move when the key does.

    Left behind, it guarded the retired key and let a second entry be created
    with the key now in use.
    """
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, unique_id=_key_id(API_KEY))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "a-second-key"}
    )
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.unique_id == _key_id("a-second-key")

    # The key now in use is guarded.
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "a-second-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"

    # The retired key is not.
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reconfiguring_with_the_same_key_is_a_no_op(
    hass, mock_client, config_entry
):
    """Re-entering the same key must not abort on this entry's own unique id."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, unique_id=_key_id(API_KEY))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.unique_id == _key_id(API_KEY)


async def test_reauth_moves_the_unique_id_too(hass, mock_client, config_entry):
    """Reauth is a key swap as well, and the hash of a new key is a new hash."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, unique_id=_key_id(API_KEY))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "renewed-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_API_KEY] == "renewed-key"
    assert config_entry.unique_id == _key_id("renewed-key")


async def test_a_swap_onto_another_entrys_key_is_refused(hass, mock_client, config_entry):
    """Two entries holding one key is what the unique id exists to prevent."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, unique_id=_key_id(API_KEY))
    other = MockConfigEntry(
        domain=DOMAIN,
        title="Jev",
        data={CONF_API_KEY: "the-other-key"},
        unique_id=_key_id("the-other-key"),
    )
    other.add_to_hass(hass)

    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "the-other-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.data[CONF_API_KEY] == API_KEY
