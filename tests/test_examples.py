"""Every example in examples/ has to be something Home Assistant would accept.

Examples rot silently: nobody runs them, and a schema change makes them wrong
without making anything fail. Running each one through the real schema is cheap and
turns them into something the test suite defends.
"""

import pathlib

import pytest
import yaml

from custom_components.jev import CONFIG_SCHEMA
from custom_components.jev.const import DOMAIN

ROOT = pathlib.Path(__file__).parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.yaml"))


def test_there_are_examples_to_check():
    assert EXAMPLES, "examples/ is empty, so the checks below prove nothing"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_an_example_is_valid_yaml(path):
    assert yaml.safe_load(path.read_text()), f"{path.name} parses to nothing"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_a_jev_block_passes_the_real_schema(path):
    """The jev: blocks are config this integration itself validates."""
    config = yaml.safe_load(path.read_text())
    if DOMAIN not in config:
        pytest.skip("no jev: block in this example")
    # Templates arrive as plain strings from yaml.safe_load, which is what Home
    # Assistant hands the schema too before it builds Template objects.
    CONFIG_SCHEMA({DOMAIN: config[DOMAIN]})


def _jev_actions(node):
    """Every step that calls a jev action, wherever the example nests it."""
    if isinstance(node, dict):
        if str(node.get("action", "")).startswith(f"{DOMAIN}."):
            yield node
        for value in node.values():
            yield from _jev_actions(value)
    elif isinstance(node, list):
        for value in node:
            yield from _jev_actions(value)


ACTION_CALLS = [
    pytest.param(step, id=f"{path.name}:{step['action']}")
    for path in EXAMPLES
    for step in _jev_actions(yaml.safe_load(path.read_text()))
]


def test_there_are_action_calls_to_check():
    assert ACTION_CALLS, "no example calls a jev action, so the check below is empty"


@pytest.mark.parametrize("step", ACTION_CALLS)
def test_an_action_call_passes_the_action_s_schema(step):
    """The action calls were checked by nobody, so a renamed field went unnoticed."""
    from custom_components.jev.models import build_question
    from custom_components.jev.services import (
        ASK_SCHEMA,
        CHOICE_SCHEMA,
        NOUL_SCHEMA,
        SCORE_SCHEMA,
    )

    schemas = {
        "noul": NOUL_SCHEMA,
        "choice": CHOICE_SCHEMA,
        "score": SCORE_SCHEMA,
        "ask": ASK_SCHEMA,
    }
    # A target sits beside data in the step, and Home Assistant merges the two.
    data = {**(step.get("data") or {}), **(step.get("target") or {})}
    validated = schemas[step["action"].removeprefix(f"{DOMAIN}.")](data)
    # jev.ask only checks that each question is a mapping. The action builds them
    # when it runs, so that is where a wrong type or a missing option shows.
    for raw in validated.get("questions", {}).values():
        build_question(raw)
