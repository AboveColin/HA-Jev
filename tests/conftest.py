"""Fixtures. Nothing here talks to TypeSafe: the client is replaced everywhere."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_API_KEY
from jevclient import ChoiceAnswer, JevResponse, NoulAnswer, ScoreAnswer, Usage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jev.const import DOMAIN

API_KEY = "test-key-not-a-real-one"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Without this the custom component is never loaded."""
    return


def build_response(**answers) -> JevResponse:
    return JevResponse(
        model="jev-1.13.0",
        answers=answers,
        usage=Usage(input_tokens=321, output_tokens=42),
        latency_ms=274.0,
    )


@pytest.fixture
def answers() -> dict:
    """One answer of each type, keyed the way the single-question actions key them."""
    return {
        "answer": NoulAnswer(noul=0.81),
    }


@pytest.fixture
def mock_client(answers):
    """Replace JevClient everywhere it is constructed."""
    client = AsyncMock()
    client.ask = AsyncMock(return_value=build_response(**answers))
    client.async_close = AsyncMock()
    with (
        patch("custom_components.jev.JevClient", return_value=client),
        patch("custom_components.jev.config_flow.JevClient", return_value=client),
    ):
        yield client


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Jev",
        data={CONF_API_KEY: API_KEY},
        options={},
        unique_id="0123456789abcdef",
    )


@pytest.fixture
async def loaded_entry(hass, mock_client, config_entry):
    """A config entry that is set up, with no YAML contexts."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


__all__ = ["ChoiceAnswer", "ScoreAnswer", "build_response"]
