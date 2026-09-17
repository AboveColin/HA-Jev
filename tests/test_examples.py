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

EXAMPLES = sorted((pathlib.Path(__file__).parent.parent / "examples").glob("*.yaml"))


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


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_an_example_names_no_real_house(path):
    """Nothing from a private network."""
    text = path.read_text().lower()
    for word in ("192.168.",):
        assert word not in text, f"{path.name} still mentions {word}"
