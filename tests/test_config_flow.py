"""The config flow, which is the one thing every user touches."""

import hashlib
from typing import ClassVar
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.data_entry_flow import FlowResultType
from jevclient import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    JevAuthError,
    JevConnectionError,
    JevValidationError,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jev.const import (
    CONF_ADVANCED,
    CONF_DAILY_TOKEN_BUDGET,
    CONF_MODEL,
    CONF_PRICE_PER_MILLION,
    DOMAIN,
)

from .conftest import API_KEY


def _form(key: str = API_KEY, url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL):
    """What the user step and the reconfigure step submit.

    The advanced section arrives as its own dict, so a flat one is not the shape
    Home Assistant hands the flow.
    """
    return {CONF_API_KEY: key, CONF_ADVANCED: {CONF_URL: url, CONF_MODEL: model}}


async def test_user_flow_creates_entry(hass, mock_client):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], _form())
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Jev"
    # Nobody who leaves the address alone gets anything other than TypeSafe.
    assert result["data"] == {
        CONF_API_KEY: API_KEY,
        CONF_URL: DEFAULT_BASE_URL,
        CONF_MODEL: DEFAULT_MODEL,
    }
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
        result["flow_id"], _form("wrong")
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    mock_client.ask.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _form())
    assert result["type"] is FlowResultType.CREATE_ENTRY


class _Answering:
    """A host that answers the probe with a status other than 200."""

    headers: ClassVar[dict[str, str]] = {}

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def json(self, content_type=None):
        return {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class _AnsweringSession:
    def __init__(self, status: int, body: str) -> None:
        self._response = _Answering(status, body)

    def post(self, url, **kwargs):
        return self._response


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (
            404,
            '{"error": {"message": "No endpoint found matching /api/v1/systemone"}}',
            "not_found",
        ),
        (500, '{"error": {"message": "internal error"}}', "cannot_connect"),
    ],
)
async def test_a_host_that_answers_404_is_not_a_host_that_cannot_be_reached(
    hass, status, body, error
):
    """A 404 says the address is wrong. Any other failure says the host is.

    A base URL carrying the request path already, which is what a reader of the
    published API docs types first, answered "could not reach the API at that
    address". The host answered perfectly well. This drives the real client so the
    message it raises is the library's own: a change there fails here. The 500 case
    holds the other side, so a broken host is not reported as a wrong address.
    """
    with patch(
        "custom_components.jev.config_flow.async_get_clientsession",
        return_value=_AnsweringSession(status, body),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            _form(url="https://openrouter.ai/api/alpha/decisions"),
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    # The not_found message names this address through a placeholder, and a
    # placeholder the form does not supply renders as the literal braces.
    assert result["description_placeholders"] == {
        "openrouter_url": "https://openrouter.ai/api"
    }


async def test_same_key_twice_is_refused(hass, mock_client, config_entry):
    config_entry.add_to_hass(hass)
    with patch("custom_components.jev.identity.hashlib.sha256") as sha:
        sha.return_value.hexdigest.return_value = config_entry.unique_id + "padding"
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _form()
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_the_key_itself_is_never_the_unique_id(hass, mock_client):
    """A unique id lands in the registry, so it must not be the credential."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(result["flow_id"], _form())
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
        result["flow_id"], _form("still-wrong")
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.ask.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form("a-fresh-key")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert loaded_entry.data[CONF_API_KEY] == "a-fresh-key"


def _entry_id(api_key: str, base_url: str = DEFAULT_BASE_URL) -> str:
    """The hash the flow computes, spelled out here rather than imported.

    An import would follow a change of the scheme silently, and a changed scheme
    is what breaks every stored id.
    """
    return hashlib.sha256(f"{base_url}\n{api_key}".encode()).hexdigest()[:16]


async def test_a_swapped_key_takes_its_unique_id_with_it(hass, mock_client, config_entry):
    """The key is half the unique id, so the id has to move when the key does.

    Left behind, it guarded the retired key and let a second entry be created
    with the key now in use.
    """
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, unique_id=_entry_id(API_KEY))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form("a-second-key")
    )
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.unique_id == _entry_id("a-second-key")

    # The key now in use is guarded.
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form("a-second-key")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"

    # The retired key is not.
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _form())
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reconfiguring_with_the_same_key_is_a_no_op(
    hass, mock_client, config_entry
):
    """Re-entering the same key must not abort on this entry's own unique id."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, unique_id=_entry_id(API_KEY))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _form())
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.unique_id == _entry_id(API_KEY)


async def test_reauth_moves_the_unique_id_too(hass, mock_client, config_entry):
    """Reauth is a key swap as well, and a new key is a new hash."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, unique_id=_entry_id(API_KEY))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "renewed-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_API_KEY] == "renewed-key"
    assert config_entry.unique_id == _entry_id("renewed-key")


async def test_a_swap_onto_another_entrys_key_is_refused(hass, mock_client, config_entry):
    """Two entries holding one key is what the unique id exists to prevent."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, unique_id=_entry_id(API_KEY))
    other = MockConfigEntry(
        domain=DOMAIN,
        title="Jev",
        data={CONF_API_KEY: "the-other-key"},
        unique_id=_entry_id("the-other-key"),
    )
    other.add_to_hass(hass)

    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form("the-other-key")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.data[CONF_API_KEY] == API_KEY


GATEWAY = "http://gateway.local:8093"


async def test_a_custom_endpoint_is_stored_and_asked(hass, mock_client):
    """The checking request has to go to the endpoint being configured.

    Validating against TypeSafe and then talking to something else would pass a key
    the endpoint has never seen.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form(url=GATEWAY)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_API_KEY: API_KEY,
        CONF_URL: GATEWAY,
        CONF_MODEL: DEFAULT_MODEL,
    }
    assert mock_client.built_by_flow.call_args.kwargs["base_url"] == GATEWAY
    assert mock_client.built_by_setup.call_args.kwargs["base_url"] == GATEWAY


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("http://gateway.local:8093/", GATEWAY),
        ("HTTP://Gateway.Local:8093", GATEWAY),
        ("  http://gateway.local:8093  ", GATEWAY),
        # A path is a prefix, because the client appends /v1/systemone to it. That is
        # what lets a reverse proxy mount the API somewhere other than the root.
        ("http://gateway.local/jev/", "http://gateway.local/jev"),
        ("", DEFAULT_BASE_URL),
    ],
)
async def test_an_endpoint_is_normalised_before_it_is_stored(
    hass, mock_client, raw, stored
):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form(url=raw)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_URL] == stored


@pytest.mark.parametrize(
    "raw",
    [
        "gateway.local:8093",
        "ftp://gateway.local",
        "http://",
        "http://gateway.local?model=jev-latest",
        "http://gateway.local#systemone",
        # yarl reads the host here as " gateway.local", which is truthy. Without an
        # explicit check the flow sends a request that can only fail.
        "https:// gateway.local",
        "https://gate way.local",
        # Credentials here would be written straight into a diagnostics file, which
        # redacts by key name and cannot see them.
        "http://someone:hunter2@gateway.local",
    ],
)
async def test_an_unusable_endpoint_is_refused_before_anything_is_asked(
    hass, mock_client, raw
):
    """The address is checked locally, so a typo costs no request and no money."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form(url=raw)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "invalid_url"}
    assert mock_client.ask.await_count == 0

    # The form is still usable, and the entry it then creates is a normal one.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form(url=GATEWAY)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reconfigure_moves_the_endpoint_and_keeps_the_entities(
    hass, mock_client, loaded_entry
):
    """Pointing an existing entry at a gateway must not mean starting over."""
    before = set(hass.states.async_entity_ids())

    result = await loaded_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    # A bad address here is refused the same way it is on the way in, and the entry
    # keeps the endpoint it already had.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form(url="gateway.local:8093")
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "invalid_url"}
    assert CONF_URL not in loaded_entry.data

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form(url=GATEWAY)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    assert loaded_entry.data[CONF_URL] == GATEWAY
    assert loaded_entry.data[CONF_API_KEY] == API_KEY
    assert set(hass.states.async_entity_ids()) == before
    assert mock_client.built_by_setup.call_args.kwargs["base_url"] == GATEWAY


async def test_clearing_the_endpoint_goes_back_to_typesafe(hass, mock_client):
    """Leaving the field empty is the way back, so it cannot be a one-way door."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Jev",
        data={CONF_API_KEY: API_KEY, CONF_URL: GATEWAY},
        unique_id=_entry_id(API_KEY, GATEWAY),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form(url="")
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data[CONF_URL] == DEFAULT_BASE_URL


async def test_reauth_leaves_the_endpoint_where_it_is(hass, mock_client):
    """A rejected key is not a moved endpoint, and reauth must not silently move it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Jev",
        data={CONF_API_KEY: API_KEY, CONF_URL: GATEWAY},
        unique_id=_entry_id(API_KEY, GATEWAY),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await entry.start_reauth_flow(hass)
    assert CONF_ADVANCED not in result["data_schema"].schema
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "renewed-key"}
    )
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()

    assert entry.data[CONF_URL] == GATEWAY
    # The key was checked against the gateway, not against TypeSafe.
    assert mock_client.built_by_flow.call_args.kwargs["base_url"] == GATEWAY


async def test_an_entry_from_before_this_option_still_means_typesafe(hass, mock_client):
    """Entries in the wild carry no address at all, and must not change behaviour."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Jev",
        data={CONF_API_KEY: API_KEY},
        unique_id=_entry_id(API_KEY),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert mock_client.built_by_setup.call_args.kwargs["base_url"] == DEFAULT_BASE_URL

    # And the address the reconfigure form offers is the one it has been using.
    assert mock_client.built_by_setup.call_args.kwargs["model"] == DEFAULT_MODEL

    # And the values the reconfigure form offers are the ones it has been using.
    result = await entry.start_reconfigure_flow(hass)
    advanced = result["data_schema"].schema[CONF_ADVANCED].schema.schema
    suggested = {key.schema: key.description["suggested_value"] for key in advanced}
    assert suggested == {CONF_URL: DEFAULT_BASE_URL, CONF_MODEL: DEFAULT_MODEL}


MODEL = "systemone-small"


async def test_a_custom_model_is_stored_and_asked(hass, mock_client):
    """The checking request has to ask for the model being configured.

    A key that works on the default model says nothing about one the endpoint may
    not serve.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form(url=GATEWAY, model=MODEL)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MODEL] == MODEL
    assert mock_client.built_by_flow.call_args.kwargs["model"] == MODEL
    assert mock_client.built_by_setup.call_args.kwargs["model"] == MODEL


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("  systemone-small  ", MODEL),
        ("", DEFAULT_MODEL),
        ("   ", DEFAULT_MODEL),
    ],
)
async def test_a_model_is_normalised_before_it_is_stored(hass, mock_client, raw, stored):
    """Leaving the field empty is the way back to the published default."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form(model=raw)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MODEL] == stored


async def test_a_model_id_with_a_space_is_refused_before_anything_is_asked(
    hass, mock_client
):
    """A pasted line rather than an id, caught without spending a request."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form(model="systemone small")
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MODEL: "invalid_model"}
    assert mock_client.ask.await_count == 0

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form(model=MODEL)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_both_fields_report_at_once(hass, mock_client):
    """Two typos are two messages, not one submit each."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form(url="gateway.local", model="two words")
    )
    assert result["errors"] == {CONF_URL: "invalid_url", CONF_MODEL: "invalid_model"}


async def test_a_model_the_endpoint_refuses_names_the_model(hass, mock_client):
    """422 is the answer to an unknown model, and blaming the key would misdirect."""
    mock_client.ask.side_effect = JevValidationError("unknown model")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form(model=MODEL)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_model"}


async def test_reconfigure_moves_the_model_and_keeps_the_entities(
    hass, mock_client, loaded_entry
):
    before = set(hass.states.async_entity_ids())

    result = await loaded_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form(model=MODEL)
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    assert loaded_entry.data[CONF_MODEL] == MODEL
    assert set(hass.states.async_entity_ids()) == before
    assert mock_client.built_by_setup.call_args.kwargs["model"] == MODEL


async def test_reauth_leaves_the_model_where_it_is(hass, mock_client):
    """Reauth shows the key alone, so it must not reset what the entry asks for."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Jev",
        data={CONF_API_KEY: API_KEY, CONF_URL: GATEWAY, CONF_MODEL: MODEL},
        unique_id=_entry_id(API_KEY, GATEWAY),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "renewed-key"}
    )
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()

    assert entry.data[CONF_MODEL] == MODEL
    assert mock_client.built_by_flow.call_args.kwargs["model"] == MODEL


async def test_an_endpoint_of_your_own_needs_no_key(hass, mock_client):
    """The key is optional because an endpoint of your own may ask for none."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form(key="", url=GATEWAY)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == ""
    assert mock_client.built_by_flow.call_args.args[0] == ""


async def test_an_empty_key_against_typesafe_is_refused(hass, mock_client):
    """The published API answers 401 to a keyless request, so no request is sent."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _form(key="")
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_API_KEY: "key_required"}
    mock_client.ask.assert_not_called()


async def test_two_keyless_endpoints_are_two_entries(hass, mock_client):
    """Hashing the key alone gave every keyless endpoint one id, so only one fitted."""
    for url in (GATEWAY, "http://192.0.2.5:8093"):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _form(key="", url=url)
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_the_same_key_at_two_endpoints_is_two_entries(hass, mock_client):
    """What the endpoint in the id costs: one key, two entries, two daily budgets.

    A key means something only at the endpoint that issued it, so the same string
    at two addresses is two credentials rather than one used twice.
    """
    for url in (DEFAULT_BASE_URL, GATEWAY):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _form(url=url)
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2
