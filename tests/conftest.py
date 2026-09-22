"""Fixtures. Nothing here talks to TypeSafe: the client is replaced everywhere."""

from unittest.mock import DEFAULT, AsyncMock, patch

import pytest
from homeassistant.const import CONF_API_KEY
from homeassistant.setup import async_setup_component
from jevclient import ChoiceAnswer, JevResponse, NoulAnswer, ScoreAnswer, Usage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jev.const import DOMAIN

API_KEY = "test-key-not-a-real-one"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Without this the custom component is never loaded."""
    return


@pytest.fixture(autouse=True)
async def homeassistant_component(hass):
    """Set up the `homeassistant` component, which a real instance always has.

    The AI Task platform depends on the conversation component, and conversation
    reads `homeassistant.exposed_entities`. Without this, setting up an entry logs
    "Setup failed for 'ai_task': Could not setup dependencies: conversation" and the
    entity is quietly missing from every test, which is the wrong thing to be
    testing against.
    """
    await async_setup_component(hass, "homeassistant", {})


def build_response(**answers) -> JevResponse:
    return JevResponse(
        model="jev-1.13.0",
        answers=answers,
        usage=Usage(input_tokens=321, output_tokens=42),
        latency_ms=274.0,
    )


PROBE_TOKENS = 40


def probe_or_default(state, questions, *args, **kwargs):
    """Answer setup's probe the way the API does, and anything else as configured.

    The probe is billed, so it lands in the day's totals. 40 tokens is the size the
    setup comment gives it, and it differs from an evaluation's 321 so a test can
    tell the two apart.
    """
    if "probe" in questions:
        return JevResponse(
            model="jev-1.13.0",
            answers={"probe": NoulAnswer(noul=0.97)},
            usage=Usage(input_tokens=PROBE_TOKENS, output_tokens=3),
            latency_ms=90.0,
        )
    return DEFAULT


@pytest.fixture
def answers() -> dict:
    """One answer of each type, keyed the way the single-question actions key them."""
    return {
        "answer": NoulAnswer(noul=0.81),
    }


@pytest.fixture
def mock_client(answers):
    """Replace JevClient everywhere it is constructed.

    `built_by_setup` and `built_by_flow` are the two patched constructors, so a test
    can assert what the client was pointed at rather than only what it was asked.
    """
    client = AsyncMock()
    client.ask = AsyncMock(return_value=build_response(**answers))
    client.ask.side_effect = probe_or_default
    client.async_close = AsyncMock()
    with (
        patch("custom_components.jev.JevClient", return_value=client) as by_setup,
        patch(
            "custom_components.jev.config_flow.JevClient", return_value=client
        ) as by_flow,
    ):
        client.built_by_setup = by_setup
        client.built_by_flow = by_flow
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
