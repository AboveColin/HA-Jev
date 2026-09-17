"""The manifest is what users install from, so its claims have to hold."""

import json
import pathlib
import re

import jevclient

ROOT = pathlib.Path(__file__).parent.parent
MANIFEST = json.loads((ROOT / "custom_components/jev/manifest.json").read_text())


def test_the_pinned_client_is_the_one_under_test():
    """A pin that drifts from the installed client makes the whole suite a lie.

    Tests run against whatever is installed. Users get exactly what the manifest
    pins. If those are different versions, everything below them proves nothing.
    """
    [requirement] = MANIFEST["requirements"]
    name, _, pinned = requirement.partition("==")
    assert name == "jevclient"
    assert pinned == jevclient.__version__, (
        f"manifest pins jevclient=={pinned} but the tests ran against "
        f"{jevclient.__version__}"
    )


def test_the_client_is_pinned_exactly():
    """A floating requirement means a library release can break every user."""
    for requirement in MANIFEST["requirements"]:
        assert "==" in requirement, f"{requirement} is not pinned exactly"


def test_hacs_minimum_matches_what_the_code_needs():
    """homeassistant.helpers.target.TargetSelection did not exist before 2026."""
    hacs = json.loads((ROOT / "hacs.json").read_text())
    major = int(hacs["homeassistant"].split(".")[0])
    assert major >= 2026, "the code imports APIs that 2025 releases do not have"


def test_the_version_is_a_release_version():
    assert re.fullmatch(r"\d+\.\d+\.\d+", MANIFEST["version"])
