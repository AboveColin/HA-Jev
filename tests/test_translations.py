"""Every translation key the code raises has to exist, in every language.

A missing key does not crash: Home Assistant falls back to showing the raw key, so
the user sees `too_many_entities` instead of a sentence. Nothing fails, which is
exactly why this needs a test.
"""

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).parent.parent
COMPONENT = ROOT / "custom_components/jev"
STRINGS = json.loads((COMPONENT / "strings.json").read_text())
TRANSLATIONS = sorted((COMPONENT / "translations").glob("*.json"))


def keys_raised_in_code() -> set[str]:
    found = set()
    for path in COMPONENT.glob("*.py"):
        found.update(re.findall(r'translation_key="([a-z_]+)"', path.read_text()))
    return found


def test_every_raised_exception_key_exists():
    declared = set(STRINGS.get("exceptions", {}))
    declared |= set(STRINGS.get("issues", {}))
    entity = STRINGS.get("entity", {})
    for platform in entity.values():
        declared |= set(platform)
    missing = keys_raised_in_code() - declared
    assert not missing, f"raised in code but absent from strings.json: {sorted(missing)}"


def test_every_language_has_the_same_keys_as_english():
    def flatten(d, prefix=""):
        out = set()
        for k, v in d.items():
            out.add(f"{prefix}{k}")
            if isinstance(v, dict):
                out |= flatten(v, f"{prefix}{k}.")
        return out

    english = flatten(STRINGS)
    for path in TRANSLATIONS:
        other = flatten(json.loads(path.read_text()))
        missing = english - other
        assert not missing, f"{path.name} is missing: {sorted(missing)[:10]}"


def test_every_translated_entity_has_an_icon():
    """Gold asks for icon translations; a key with no icon silently falls back."""
    icons = json.loads((COMPONENT / "icons.json").read_text())["entity"]
    for platform, entities in STRINGS["entity"].items():
        for key in entities:
            assert key in icons.get(platform, {}), f"{platform}.{key} has no icon"
