"""jev.house_check: Repairs cards for what looks wrong, and a way back from each fix."""

from datetime import timedelta

import pytest
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.const import CONF_API_KEY
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from jevclient import JevError, JevResponse, NoulAnswer, Usage
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.jev.const import (
    CONF_DAILY_TOKEN_BUDGET,
    CONF_HOUSE_CHECK_WEEKLY,
    DOMAIN,
    HOUSE_CHECK_HOUR,
    LOOKS_WRONG_MIN_NOUL,
)

from .conftest import API_KEY, probe_or_default

LAMP = "light.living_room"
PORCH = "light.porch"
PLUG = "switch.kettle"
LOCK = "lock.front_door"
BLIND = "cover.bedroom_blind"


def issues(hass) -> dict[str, ir.IssueEntry]:
    return {
        issue_id: issue
        for (domain, issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN
    }


def kinds(hass) -> list[tuple[str, str]]:
    """Each open card as (text, first entity), which is what a test cares about."""
    return sorted(
        (issue.translation_key, issue.translation_placeholders["entity_id"])
        for issue in issues(hass).values()
        if issue.is_fixable
    )


async def house_check(hass, **data):
    return await hass.services.async_call(
        DOMAIN, "house_check", data, blocking=True, return_response=True
    )


def wrong_are(mock_client, *names):
    """Answer the house check the way a model would: yes for these names only."""

    async def answer(state, questions, *args, **kwargs):
        if "probe" in questions:
            return probe_or_default(state, questions)
        by_key = {e["id"]: e["name"] for e in state["entities"]}
        return JevResponse(
            model="jev-1.13.0",
            answers={
                key: NoulAnswer(noul=0.9 if by_key[key] in names else 0.1)
                for key in questions
            },
            usage=Usage(input_tokens=500, output_tokens=20),
            latency_ms=300.0,
        )

    mock_client.ask.side_effect = answer


@pytest.fixture
def switched(hass):
    """Fake turn_on and turn_off for lights and switches, which set the state.

    The real platforms act on entities, and these states have none behind them.
    """
    calls: list[ServiceCall] = []

    async def turn(call: ServiceCall) -> None:
        calls.append(call)
        # No schema here, so one entity id arrives as a string, several as a list.
        for entity_id in cv.ensure_list(call.data["entity_id"]):
            old = hass.states.get(entity_id)
            attributes = dict(old.attributes) if old else {}
            if call.service == "turn_on" and "brightness" in call.data:
                attributes["brightness"] = call.data["brightness"]
            hass.states.async_set(
                entity_id, "on" if call.service == "turn_on" else "off", attributes
            )

    for domain in ("light", "switch", "fan"):
        for service in ("turn_on", "turn_off"):
            hass.services.async_register(domain, service, turn)
    return calls


@pytest.fixture
async def house(hass, mock_client, config_entry, switched):
    """Five exposed entities: two lights, a plug, a lock and a blind.

    The snapshot leaves the lock out, so the Jev check sees four.
    """
    assert await async_setup_component(hass, "conversation", {})
    assert await async_setup_component(hass, "repairs", {})
    entities = er.async_get(hass)
    for entity_id, name, state, attributes in (
        (LAMP, "Living room lamp", "on", {"brightness": 120}),
        (PORCH, "Porch light", "off", {}),
        (PLUG, "Kettle", "on", {}),
        (LOCK, "Front door", "unlocked", {}),
        (BLIND, "Bedroom blind", "open", {}),
    ):
        domain, object_id = entity_id.split(".")
        entities.async_get_or_create(
            domain, "demo", object_id, suggested_object_id=object_id
        )
        hass.states.async_set(entity_id, state, {"friendly_name": name, **attributes})
        async_expose_entity(hass, conversation.DOMAIN, entity_id, True)

    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def fix(hass, issue_id, choice):
    """Open a card's fix flow and pick one of its menu options."""
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    if choice is None:
        return result
    return await manager.async_configure(result["flow_id"], {"next_step_id": choice})


def only_issue_for(hass, entity_id) -> str:
    [issue_id] = [
        i
        for i, issue in issues(hass).items()
        if issue.is_fixable and issue.translation_placeholders["entity_id"] == entity_id
    ]
    return issue_id


# --- The checks that cost nothing ---


async def test_a_week_unavailable_opens_a_card_and_six_days_does_not(
    hass, house, mock_client, freezer
):
    hass.states.async_set("sensor.old_plug", "unavailable", {"friendly_name": "Old plug"})
    freezer.tick(timedelta(days=1))
    hass.states.async_set("sensor.new_plug", "unavailable", {"friendly_name": "New plug"})
    freezer.tick(timedelta(days=6))
    calls = mock_client.ask.call_count

    result = await house_check(hass, use_jev=False)

    assert kinds(hass) == [("house_check_unavailable", "sensor.old_plug")]
    assert result["findings"] == [
        {
            "kind": "unavailable",
            "name": "Old plug",
            "entity_ids": ["sensor.old_plug"],
            "state": "unavailable",
            "value": "7",
        }
    ]
    # The free checks send nothing.
    assert mock_client.ask.call_count == calls


def unavailable_since_last_week(hass, freezer, config_entry, *names) -> list[str]:
    """Register these entities under the config entry and let a week pass."""
    registry = er.async_get(hass)
    entity_ids = []
    for name in names:
        entry = registry.async_get_or_create(
            "sensor", "demo", name, config_entry=config_entry, original_name=name
        )
        hass.states.async_set(entry.entity_id, "unavailable", {"friendly_name": name})
        entity_ids.append(entry.entity_id)
    freezer.tick(timedelta(days=7))
    return entity_ids


async def test_one_integration_gone_is_one_card_not_one_per_entity(hass, house, freezer):
    server = MockConfigEntry(domain="demo", title="Old server")
    server.add_to_hass(hass)
    gone = unavailable_since_last_week(hass, freezer, server, "D", "C", "B", "A")
    hass.states.async_set("sensor.yaml_plug", "unavailable", {"friendly_name": "Plug"})
    freezer.tick(timedelta(days=7))

    result = await house_check(hass, use_jev=False)

    assert kinds(hass) == [
        ("house_check_unavailable", "sensor.yaml_plug"),
        ("house_check_unavailable_many", sorted(gone)[0]),
    ]
    [card] = [
        i for i in issues(hass).values() if i.translation_placeholders["count"] == "4"
    ]
    assert card.translation_placeholders["name"] == "Old server"
    assert card.translation_placeholders["examples"] == "A, B, C"
    [many] = [f for f in result["findings"] if f["name"] == "Old server"]
    assert many["entity_ids"] == sorted(gone)

    menu = await fix(hass, card.issue_id, None)
    assert menu["menu_options"] == ["ignore", "snooze"]
    assert menu["description_placeholders"]["count"] == "4"


async def test_a_card_of_one_names_the_entity_not_its_integration(hass, house, freezer):
    server = MockConfigEntry(domain="demo", title="Old server")
    server.add_to_hass(hass)
    [gone] = unavailable_since_last_week(hass, freezer, server, "Attic sensor")

    await house_check(hass, use_jev=False)

    assert kinds(hass) == [("house_check_unavailable", gone)]
    [card] = issues(hass).values()
    assert card.translation_placeholders["name"] == "Attic sensor"


async def test_ignoring_a_group_card_quiets_its_entities_and_not_new_ones(
    hass, house, freezer
):
    server = MockConfigEntry(domain="demo", title="Old server")
    server.add_to_hass(hass)
    unavailable_since_last_week(hass, freezer, server, "A", "B")
    await house_check(hass, use_jev=False)
    [card] = issues(hass)

    await fix(hass, card, "ignore")
    assert issues(hass) == {}
    await house_check(hass, use_jev=False)
    assert issues(hass) == {}

    [later] = unavailable_since_last_week(hass, freezer, server, "C")
    await house_check(hass, use_jev=False)
    assert kinds(hass) == [("house_check_unavailable", later)]


async def test_snoozing_a_group_card_brings_it_back_after_30_days(hass, house, freezer):
    server = MockConfigEntry(domain="demo", title="Old server")
    server.add_to_hass(hass)
    gone = unavailable_since_last_week(hass, freezer, server, "A", "B")
    await house_check(hass, use_jev=False)
    [card] = issues(hass)

    await fix(hass, card, "snooze")
    freezer.tick(timedelta(days=29))
    await house_check(hass, use_jev=False)
    assert issues(hass) == {}
    freezer.tick(timedelta(days=2))
    await house_check(hass, use_jev=False)
    assert kinds(hass) == [("house_check_unavailable_many", sorted(gone)[0])]


async def test_a_battery_below_ten_percent_opens_a_card(hass, house):
    for entity_id, value, device_class, unit in (
        ("sensor.door_battery", "5", "battery", "%"),
        ("sensor.remote_battery", "50", "battery", "%"),
        ("sensor.old_remote_battery", "4", "battery", "V"),
        ("sensor.humidity", "5", "humidity", "%"),
        ("sensor.bad_battery", "not a number", "battery", "%"),
        ("binary_sensor.leak_battery", "on", "battery", None),
        ("binary_sensor.window_battery", "off", "battery", None),
    ):
        attributes = {"device_class": device_class}
        if unit:
            attributes["unit_of_measurement"] = unit
        hass.states.async_set(entity_id, value, attributes)

    result = await house_check(hass, use_jev=False)

    assert kinds(hass) == [
        ("house_check_low_battery", "binary_sensor.leak_battery"),
        ("house_check_low_battery", "sensor.door_battery"),
    ]
    values = {f["entity_ids"][0]: f["value"] for f in result["findings"]}
    assert values == {"sensor.door_battery": "5 %", "binary_sensor.leak_battery": "on"}


async def test_a_card_closes_when_its_condition_clears(hass, house):
    hass.states.async_set(
        "sensor.door_battery",
        "5",
        {"device_class": "battery", "unit_of_measurement": "%"},
    )
    await house_check(hass, use_jev=False)
    assert kinds(hass) == [("house_check_low_battery", "sensor.door_battery")]

    hass.states.async_set(
        "sensor.door_battery",
        "100",
        {"device_class": "battery", "unit_of_measurement": "%"},
    )
    await house_check(hass, use_jev=False)

    assert kinds(hass) == []


# --- The Jev check ---


async def test_one_request_asks_about_every_exposed_entity_and_one_card_opens(
    hass, house, mock_client
):
    wrong_are(mock_client, "Living room lamp")

    result = await house_check(hass)

    assert kinds(hass) == [("house_check_looks_wrong", LAMP)]
    [finding] = result["findings"]
    assert finding["value"] == "0.90"
    state, questions = mock_client.ask.call_args.args
    assert sorted(e["name"] for e in state["entities"]) == [
        "Bedroom blind",
        "Kettle",
        "Living room lamp",
        "Porch light",
    ]
    assert len(questions) == 4
    assert "now" in state


async def test_an_answer_just_under_the_threshold_opens_no_card(hass, house, mock_client):
    async def answer(state, questions, *args, **kwargs):
        return JevResponse(
            model="jev-1.13.0",
            answers={k: NoulAnswer(noul=LOOKS_WRONG_MIN_NOUL - 0.01) for k in questions},
            usage=Usage(input_tokens=500, output_tokens=20),
            latency_ms=300.0,
        )

    mock_client.ask.side_effect = answer
    assert (await house_check(hass))["findings"] == []


async def test_an_unavailable_entity_is_described_but_not_asked_about(
    hass, house, mock_client
):
    hass.states.async_set(PORCH, "unavailable", {"friendly_name": "Porch light"})
    wrong_are(mock_client)

    await house_check(hass)

    state, questions = mock_client.ask.call_args.args
    porch = next(e for e in state["entities"] if e["name"] == "Porch light")
    assert porch["id"] not in questions
    assert len(questions) == 3


async def test_a_spent_budget_still_publishes_the_free_cards(hass, mock_client, switched):
    """The Jev part fails loudly, and the battery card it did not need still opens."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: API_KEY},
        options={CONF_DAILY_TOKEN_BUDGET: 50},
        unique_id="0123456789abcdef",
    )
    assert await async_setup_component(hass, "conversation", {})
    hass.states.async_set(LAMP, "on", {"friendly_name": "Living room lamp"})
    async_expose_entity(hass, conversation.DOMAIN, LAMP, True)
    hass.states.async_set(
        "sensor.door_battery",
        "5",
        {"device_class": "battery", "unit_of_measurement": "%"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError) as err:
        await house_check(hass)

    assert err.value.translation_key == "action_over_budget"
    assert kinds(hass) == [("house_check_low_battery", "sensor.door_battery")]


# --- What a card can do ---


async def test_turning_a_lamp_off_from_its_card_and_undoing_it_puts_it_back(
    hass, house, mock_client
):
    wrong_are(mock_client, "Living room lamp")
    await house_check(hass)
    issue_id = only_issue_for(hass, LAMP)

    menu = await fix(hass, issue_id, None)
    assert menu["menu_options"] == ["ignore", "snooze", "turn_off"]
    assert menu["description_placeholders"] == {
        "name": "Living room lamp",
        "entity_id": LAMP,
        "state": "on",
        "value": "0.90",
        "count": "1",
        "examples": "",
    }
    result = await fix(hass, issue_id, "turn_off")
    assert result["type"] == "create_entry"
    assert hass.states.get(LAMP).state == "off"
    assert issues(hass) == {}

    response = await hass.services.async_call(
        DOMAIN, "undo_house_check", {}, blocking=True, return_response=True
    )

    assert response == {"restored": [LAMP]}
    lamp = hass.states.get(LAMP)
    assert lamp.state == "on"
    assert lamp.attributes["brightness"] == 120
    # Undone once is undone: a second undo has nothing left to put back.
    again = await hass.services.async_call(
        DOMAIN, "undo_house_check", {}, blocking=True, return_response=True
    )
    assert again == {"restored": []}


async def test_undo_takes_only_the_entities_it_names(hass, house, mock_client):
    wrong_are(mock_client, "Living room lamp", "Kettle")
    await house_check(hass)
    await fix(hass, only_issue_for(hass, LAMP), "turn_off")
    await fix(hass, only_issue_for(hass, PLUG), "turn_off")

    response = await hass.services.async_call(
        DOMAIN,
        "undo_house_check",
        {"entity_id": PLUG},
        blocking=True,
        return_response=True,
    )

    assert response == {"restored": [PLUG]}
    assert hass.states.get(PLUG).state == "on"
    assert hass.states.get(LAMP).state == "off"


async def test_a_lock_is_never_asked_about_and_a_blind_is_never_turned_off(
    hass, house, mock_client
):
    wrong_are(mock_client, "Front door", "Bedroom blind")
    await house_check(hass)

    state, _ = mock_client.ask.call_args.args
    assert "Front door" not in [e["name"] for e in state["entities"]]
    assert kinds(hass) == [("house_check_looks_wrong", BLIND)]
    menu = await fix(hass, only_issue_for(hass, BLIND), None)
    assert menu["menu_options"] == ["ignore", "snooze"]
    for entity_id in (LOCK, BLIND):
        with pytest.raises(HomeAssistantError) as err:
            await house.runtime_data.house_check.async_turn_off(entity_id, None)
        assert err.value.translation_key == "house_check_cannot_turn_off"
    assert hass.states.get(LOCK).state == "unlocked"
    assert hass.states.get(BLIND).state == "open"


@pytest.mark.parametrize("entity_id", ["siren.hallway", "humidifier.bedroom"])
async def test_on_is_not_enough_outside_fans_lights_and_switches(hass, house, entity_id):
    hass.states.async_set(entity_id, "on")
    check = house.runtime_data.house_check
    assert not check.can_turn_off(entity_id)
    with pytest.raises(HomeAssistantError):
        await check.async_turn_off(entity_id, None)
    assert check.undo == {}


async def test_an_ignored_entity_is_not_asked_about_again(hass, house, mock_client):
    wrong_are(mock_client, "Living room lamp")
    await house_check(hass)
    await fix(hass, only_issue_for(hass, LAMP), "ignore")
    assert issues(hass) == {}

    await house_check(hass)

    assert issues(hass) == {}
    state, questions = mock_client.ask.call_args.args
    lamp = next(e for e in state["entities"] if e["name"] == "Living room lamp")
    assert lamp["id"] not in questions


async def test_a_snoozed_entity_comes_back_after_thirty_days(
    hass, house, mock_client, freezer
):
    wrong_are(mock_client, "Living room lamp")
    await house_check(hass)
    await fix(hass, only_issue_for(hass, LAMP), "snooze")

    freezer.tick(timedelta(days=29))
    await house_check(hass)
    assert issues(hass) == {}

    freezer.tick(timedelta(days=2))
    await house_check(hass)
    assert kinds(hass) == [("house_check_looks_wrong", LAMP)]


async def test_what_the_user_chose_survives_a_reload(hass, house, mock_client):
    wrong_are(mock_client, "Living room lamp", "Kettle")
    await house_check(hass)
    await fix(hass, only_issue_for(hass, LAMP), "turn_off")
    await fix(hass, only_issue_for(hass, PLUG), "ignore")

    assert await hass.config_entries.async_reload(house.entry_id)
    await hass.async_block_till_done()
    check = house.runtime_data.house_check

    assert check.ignored == {PLUG}
    assert set(check.undo) == {LAMP}


async def test_the_cards_survive_a_restart_and_go_with_the_entry(
    hass, house, hass_storage
):
    hass.states.async_set(
        "sensor.door_battery",
        "5",
        {"device_class": "battery", "unit_of_measurement": "%"},
    )
    await house_check(hass, use_jev=False)
    [card] = issues(hass).values()
    assert card.is_persistent

    assert await hass.config_entries.async_remove(house.entry_id)
    await hass.async_block_till_done()
    assert issues(hass) == {}
    assert f"{DOMAIN}.{house.entry_id}.house_check" not in hass_storage


async def test_a_card_on_an_unloaded_entry_aborts(hass, house, mock_client):
    wrong_are(mock_client, "Living room lamp")
    await house_check(hass)
    issue_id = only_issue_for(hass, LAMP)
    assert await hass.config_entries.async_unload(house.entry_id)

    result = await fix(hass, issue_id, None)

    assert result["type"] == "abort"
    assert result["reason"] == "not_loaded"
    assert hass.states.get(LAMP).state == "on"


@pytest.mark.parametrize("choice", ["ignore", "snooze", "turn_off"])
async def test_a_choice_made_after_the_entry_unloads_does_nothing(
    hass, house, mock_client, choice
):
    wrong_are(mock_client, "Living room lamp")
    await house_check(hass)
    issue_id = only_issue_for(hass, LAMP)
    manager = repairs_flow_manager(hass)
    menu = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert await hass.config_entries.async_unload(house.entry_id)

    result = await manager.async_configure(menu["flow_id"], {"next_step_id": choice})

    assert result["type"] == "abort"
    assert result["reason"] == "not_loaded"
    assert hass.states.get(LAMP).state == "on"
    assert issue_id in issues(hass)


async def test_a_house_with_nothing_to_ask_sends_no_request(hass, house, mock_client):
    house.runtime_data.house_check.ignored.update((LAMP, PORCH, PLUG, BLIND))
    calls = mock_client.ask.call_count

    assert (await house_check(hass))["findings"] == []
    assert mock_client.ask.call_count == calls


# --- The weekly run ---


@pytest.fixture
def weekly_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: API_KEY},
        options={CONF_HOUSE_CHECK_WEEKLY: True},
        unique_id="0123456789abcdef",
    )


def house_checks_sent(mock_client) -> int:
    return sum(
        1
        for call in mock_client.ask.call_args_list
        if isinstance(call.args[0], dict) and "entities" in call.args[0]
    )


async def next_morning(hass, freezer, days=1):
    now = dt_util.as_local(dt_util.utcnow())
    target = (now + timedelta(days=days)).replace(
        hour=HOUSE_CHECK_HOUR, minute=0, second=0, microsecond=0
    )
    freezer.move_to(target)
    async_fire_time_changed(hass, target)
    await hass.async_block_till_done()


async def test_the_weekly_run_asks_once_a_week(
    hass, mock_client, weekly_entry, freezer, switched
):
    assert await async_setup_component(hass, "conversation", {})
    hass.states.async_set(LAMP, "on", {"friendly_name": "Living room lamp"})
    async_expose_entity(hass, conversation.DOMAIN, LAMP, True)
    weekly_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(weekly_entry.entry_id)
    await hass.async_block_till_done()
    wrong_are(mock_client)

    await next_morning(hass, freezer)
    assert house_checks_sent(mock_client) == 1
    for _ in range(6):
        await next_morning(hass, freezer)
    assert house_checks_sent(mock_client) == 1
    await next_morning(hass, freezer)
    assert house_checks_sent(mock_client) == 2


async def test_the_weekly_run_is_off_unless_chosen(hass, house, mock_client, freezer):
    wrong_are(mock_client)
    for _ in range(8):
        await next_morning(hass, freezer)
    assert house_checks_sent(mock_client) == 0


async def test_a_weekly_run_that_fails_opens_a_card_and_retries_next_morning(
    hass, mock_client, weekly_entry, freezer, switched
):
    assert await async_setup_component(hass, "conversation", {})
    hass.states.async_set(LAMP, "on", {"friendly_name": "Living room lamp"})
    async_expose_entity(hass, conversation.DOMAIN, LAMP, True)
    weekly_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(weekly_entry.entry_id)
    await hass.async_block_till_done()
    mock_client.ask.side_effect = JevError("the service is down")

    await next_morning(hass, freezer)

    [failed] = issues(hass).values()
    assert failed.translation_key == "house_check_failed"
    assert failed.is_fixable is False
    assert "the service is down" in failed.translation_placeholders["reason"]

    wrong_are(mock_client)
    await next_morning(hass, freezer)
    assert issues(hass) == {}


# --- The recorder reaches past a restart ---


@pytest.fixture
def mock_recorder_before_hass(async_test_recorder):
    """The recorder's database has to exist before hass starts."""


async def test_the_recorder_finds_a_week_unavailable_across_a_restart(
    recorder_mock, hass, house, freezer
):
    """last_changed starts again at a restart. The recorder remembers the week."""
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    hass.states.async_set("sensor.old_plug", "unavailable", {"friendly_name": "Old plug"})
    hass.states.async_set("sensor.moved_plug", "on", {"friendly_name": "Moved plug"})
    await async_wait_recording_done(hass)
    freezer.tick(timedelta(days=2))
    hass.states.async_set(
        "sensor.moved_plug", "unavailable", {"friendly_name": "Moved plug"}
    )
    await async_wait_recording_done(hass)
    freezer.tick(timedelta(days=6))
    # What a restart looks like from here: the same state, with a new last_changed.
    for entity_id in ("sensor.old_plug", "sensor.moved_plug"):
        hass.states.async_set(
            entity_id, "unavailable", {"friendly_name": "x"}, force_update=True
        )
    await async_wait_recording_done(hass)

    await house_check(hass, use_jev=False)

    assert kinds(hass) == [("house_check_unavailable", "sensor.old_plug")]
