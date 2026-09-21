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


# Words that only appear in the author's own house or on the author's own network.
# A comment or a doc line that names one of them publishes a private detail that
# cannot be unpublished, so the check covers the shipped tree, not the examples
# alone.
LEAKS = (
    "192.168.",
    "bijkeuken",
    "wasmachine",
    "nix1",
    "nix2",
    "hunky",
    "ha-dev",
)

SHIPPED = [
    path
    for folder in ("custom_components", "site-docs")
    for path in sorted((ROOT / folder).rglob("*"))
    if path.is_file() and path.suffix in {".py", ".md", ".json", ".yaml"}
]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_an_example_names_no_real_house(path):
    """No entity from the author's own setup, and nothing from a private network."""
    text = path.read_text().lower()
    for leak in LEAKS:
        assert leak not in text, f"{path.name} still mentions {leak}"


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_the_shipped_tree_names_no_real_house(path):
    """Same for the code and the documentation, where a measurement gets written."""
    text = path.read_text().lower()
    for leak in LEAKS:
        assert leak not in text, f"{path} still mentions {leak}"
