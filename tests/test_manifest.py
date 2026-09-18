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


def test_the_conversation_requirements_match_what_home_assistant_pins():
    """The conversation component brings its own dependencies, and CI gets none of them.

    A test environment installs what requirements-test.txt asks for and nothing
    else, so the conversation component's own pins have to be copied there by hand.
    This caught it the expensive way once: the local venv had them installed
    ad hoc, CI did not, and the whole test module failed to import with
    ModuleNotFoundError: No module named 'hassil'.

    Pinning the same versions Home Assistant pins means the suite runs against what
    a user runs. This asserts the two lists have not drifted apart.
    """
    import homeassistant.components.conversation as conversation_component

    component_manifest = json.loads(
        (
            pathlib.Path(conversation_component.__file__).parent / "manifest.json"
        ).read_text()
    )
    required = set(component_manifest["requirements"])
    ours = {
        line.strip()
        for line in (ROOT / "requirements-test.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    missing = required - ours
    assert not missing, (
        f"requirements-test.txt is missing {sorted(missing)}, which the conversation "
        f"component pins. CI will fail to import the conversation platform."
    )
