"""Every blueprint in blueprints/ loads, and does what its description says.

A blueprint is a template for someone else's automation, so nobody on this side runs
it. Each one here is instantiated with test inputs, its real trigger is fired, Jev's
answer is mocked, and the test checks which of the user's actions ran and what they
could read. The cases are in tests/blueprints/.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import urllib.parse

import pytest
from homeassistant.components.blueprint import models
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml
from pytest_homeassistant_custom_component.common import async_mock_service

from .blueprints import CASES, Case
from .conftest import build_response

ROOT = pathlib.Path(__file__).parent.parent
FOLDER = ROOT / "blueprints" / "automation" / "jev"
BLUEPRINTS = sorted(FOLDER.glob("*.yaml"))
SOURCE = "https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/"
IMPORT = "https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url="
PAGE = ROOT / "site-docs" / "blueprints.md"


def test_there_are_blueprints_to_check():
    assert BLUEPRINTS, "blueprints/ is empty, so the checks below prove nothing"


def test_every_blueprint_has_a_case():
    """A blueprint with no case would pass every test below by being skipped."""
    names = {p.stem for p in BLUEPRINTS}
    # A blueprint with several branches has one case per branch, "name:branch".
    covered = {key.split(":")[0] for key in CASES}
    assert names - covered == set(), "blueprints with no case"
    assert covered - names == set(), "cases for blueprints that do not exist"


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda p: p.stem)
def test_a_blueprint_is_one_home_assistant_accepts(path):
    blueprint = models.Blueprint(
        load_yaml(path), expected_domain="automation", schema=_schema()
    )
    meta = blueprint.metadata
    assert meta["name"].startswith("Jev - ")
    # The import button fetches this URL, and so does "re-import" later.
    assert meta["source_url"] == SOURCE + path.name
    assert meta["homeassistant"]["min_version"] == "2026.9.0"


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda p: p.stem)
def test_every_blueprint_has_an_import_button(path):
    """The page lists each blueprint with a button that imports this file."""
    url = IMPORT + urllib.parse.quote(SOURCE + path.name, safe="")
    assert f"]({url})" in PAGE.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda p: p.stem)
def test_every_description_says_what_it_costs(path):
    """A user picks a blueprint by its bill, so the description gives the number."""
    description = " ".join(load_yaml(path)["blueprint"]["description"].split())
    assert re.search(r"\b(\d+|one) calls? (a|per) (day|week)\b", description), (
        "the description has no 'N calls a day'"
    )
    assert ";" not in description, "the description has a semicolon"


def _schema():
    from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA

    return AUTOMATION_BLUEPRINT_SCHEMA


@pytest.fixture
async def blueprint_home(hass, tmp_path, loaded_entry):
    """A config directory holding this repository's blueprints."""
    hass.config.config_dir = str(tmp_path)
    shutil.copytree(FOLDER, tmp_path / "blueprints" / "automation" / "jev")
    yield tmp_path
    # A time trigger keeps a timer that Home Assistant's stop does not cancel, and
    # the test harness fails a test that leaves one. Turning off detaches triggers.
    if hass.services.has_service("automation", "turn_off"):
        await hass.services.async_call(
            "automation", "turn_off", {"entity_id": "all"}, blocking=True
        )


@pytest.mark.parametrize("name", sorted(CASES), ids=str)
async def test_a_blueprint_runs(hass, blueprint_home, mock_client, freezer, name):
    case: Case = CASES[name]
    for entity_id, (state, attributes) in case.states.items():
        hass.states.async_set(entity_id, state, attributes)
    # The entry's setup probe is already counted, and is not the blueprint's.
    mock_client.ask.reset_mock()
    mock_client.ask.side_effect = None
    mock_client.ask.return_value = build_response(**case.answers)
    calls = {s: async_mock_service(hass, "test", s) for s in ("yes", "no", "other")}

    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "id": name,
                "use_blueprint": {
                    "path": f"jev/{name.split(':')[0]}.yaml",
                    "input": case.inputs,
                },
            }
        },
    )
    await hass.async_block_till_done()
    assert hass.states.async_all("automation"), "the blueprint was refused"

    await case.fire(hass, freezer)
    await hass.async_block_till_done()

    asked = mock_client.ask.call_count
    assert asked == case.asks, f"Jev was asked {asked} times, expected {case.asks}"
    ran = {s: [c.data for c in calls[s]] for s in calls if calls[s]}
    assert ran == case.expect
    if case.check_request:
        case.check_request(*mock_client.ask.call_args.args)
