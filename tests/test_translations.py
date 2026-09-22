"""Every translation key the code raises has to exist, in every language.

A missing key does not crash: Home Assistant falls back to showing the raw key, so
the user sees `too_many_entities` instead of a sentence. Nothing fails, which is
exactly why this needs a test.
"""

import ast
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


# The exceptions Home Assistant shows a person: in a service call's error, in the
# entry's state on the integrations page, and in a coordinator's log line. vol.Invalid
# and ValueError are left out: voluptuous and the config flow turn those into form
# errors of their own.
SHOWN_EXCEPTIONS = {
    "HomeAssistantError",
    "ServiceValidationError",
    "ConfigEntryError",
    "ConfigEntryNotReady",
    "ConfigEntryAuthFailed",
    "UpdateFailed",
}


def test_no_shown_exception_is_raised_with_a_bare_string():
    """A bare string stays English in a Dutch install. A key does not."""
    bare = []
    for path in sorted(COMPONENT.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
                continue
            func = node.exc.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            keywords = {keyword.arg for keyword in node.exc.keywords}
            if name in SHOWN_EXCEPTIONS and "translation_key" not in keywords:
                bare.append(f"{path.name}:{node.lineno} {name}")
    assert not bare, f"raised without a translation key: {bare}"


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


def test_the_speech_fallbacks_match_the_english_strings():
    """A fallback that drifts from strings.json is a second wording nobody edits."""
    from custom_components.jev.conversation import _FALLBACK

    strings = json.loads((COMPONENT / "strings.json").read_text())["common"]
    # common carries the question preview's sentences too now, so the agent
    # checks its own keys rather than the whole section.
    for key, text in _FALLBACK.items():
        assert strings[key] == text, f"{key} drifted from strings.json"


def test_every_language_carries_the_agent_speech():
    """A Dutch pipeline that answers in English is only half translated."""
    english = json.loads((COMPONENT / "strings.json").read_text())["common"]
    for path in sorted((COMPONENT / "translations").glob("*.json")):
        speech = json.loads(path.read_text()).get("common")
        assert speech is not None, f"{path.name} has no common strings"
        missing = set(english) - set(speech)
        assert not missing, f"{path.name} is missing {sorted(missing)}"
        for key, text in english.items():
            if "{name}" in text:
                assert "{name}" in speech[key], (
                    f"{path.name}:{key} dropped the name placeholder"
                )


def test_every_language_keeps_every_placeholder():
    """A dropped placeholder is a KeyError at speech time, or a hole in a sentence.

    `{name}` is checked above for the agent's own lines. This is the same check over
    every string in every file, including the ones the service and subentry forms
    show, and it also holds the markdown and the paragraph breaks still, because a
    lost `**` or `\\n\\n` shows up as literal asterisks or one wall of text.
    """

    def leaves(node, prefix=""):
        if isinstance(node, dict):
            for key, value in node.items():
                yield from leaves(value, f"{prefix}{key}.")
        else:
            yield prefix.rstrip("."), node

    english = dict(leaves(STRINGS))
    for path in TRANSLATIONS:
        other = dict(leaves(json.loads(path.read_text())))
        for key, source in english.items():
            if key not in other:
                continue  # key parity is the test above's job
            target = other[key]
            assert sorted(re.findall(r"\{[a-z_]+\}", source)) == sorted(
                re.findall(r"\{[a-z_]+\}", target)
            ), f"{path.name}:{key} changed its placeholders"
            assert source.count("**") == target.count("**"), (
                f"{path.name}:{key} lost or gained markdown bold"
            )
            assert source.count("\n") == target.count("\n"), (
                f"{path.name}:{key} changed its paragraph breaks"
            )


def _unlabelled(schema, strings: dict) -> list[str]:
    """Fields with no label or no help text where the frontend looks for them.

    A field inside a section is labelled under that section, not under the step. A
    label at step level for such a field is never read, and the form shows the raw
    key instead.
    """
    from homeassistant.data_entry_flow import section

    missing = []
    for key, value in schema.schema.items():
        name = str(key.schema)
        if isinstance(value, section):
            inner = strings.get("sections", {}).get(name, {})
            missing += [f"{name}.{m}" for m in _unlabelled(value.schema, inner)]
            continue
        missing += [
            f"{part}.{name}"
            for part in ("data", "data_description")
            if name not in strings.get(part, {})
        ]
    return missing


async def test_every_form_field_has_a_label_and_help_text(hass, mock_client):
    from homeassistant.config_entries import SOURCE_USER
    from homeassistant.const import CONF_API_KEY
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.jev.const import DOMAIN, SUBENTRY_QUESTION

    from .test_subentry import question

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "test-key-not-a-real-one"},
        subentries_data=[
            question("Yes or no"),
            question("Which", kind="choice", options="a: one\nb: two"),
            question("How much", kind="score", levels="Low\nHigh"),
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    forms = [
        (
            "config",
            await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            ),
        ),
        ("config", await entry.start_reauth_flow(hass)),
        ("config", await entry.start_reconfigure_flow(hass)),
        ("options", await hass.config_entries.options.async_init(entry.entry_id)),
    ]
    for kind in ("noul", "choice", "score"):
        menu = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_QUESTION), context={"source": SOURCE_USER}
        )
        forms.append(
            (
                "config_subentries",
                await hass.config_entries.subentries.async_configure(
                    menu["flow_id"], {"next_step_id": kind}
                ),
            )
        )
    for subentry_id in entry.subentries:
        forms.append(
            (
                "config_subentries",
                await entry.start_subentry_reconfigure_flow(hass, subentry_id),
            )
        )

    missing = {}
    for area, form in forms:
        steps = STRINGS[area]
        if area == "config_subentries":
            steps = steps[SUBENTRY_QUESTION]
        step = steps["step"][form["step_id"]]
        if gaps := _unlabelled(form["data_schema"], step):
            missing.setdefault(f"{area}.{form['step_id']}", set()).update(gaps)
    assert not missing, missing
