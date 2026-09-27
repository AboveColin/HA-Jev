"""Cases for the blueprints about around the house and its appliances."""

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from jevclient import ChoiceAnswer, NoulAnswer, ScoreAnswer
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .kit import NO_WAIT, Case, at, change, run

FRIDGE_LEGEND = {
    "0": "Nothing to worry about, it will settle on its own",
    "1": "Worth a look this week",
    "2": "Check it today",
    "3": "Act now, food is at risk",
}

BATTERY_LEGEND = {
    "0": "Whenever it suits, there is no rush",
    "1": "Within the next few weeks",
    "2": "Within the next few days",
    "3": "Today",
}

ALL_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS = {name: index for index, name in enumerate(ALL_WEEKDAYS)}


async def settle() -> None:
    """Let a run reach its next wait.

    hass.async_block_till_done() waits for every automation run, so it never
    returns while a run sits in a wait_for_trigger. The mocked Jev answers in a
    few loop turns, and 50 is well past that. A case that expects the second
    question fails if this is too short.
    """
    for _ in range(50):
        await asyncio.sleep(0)


def steps(*items: tuple[Any, ...] | float) -> Callable:
    """Set states and move the clock in order.

    A number moves the clock that many minutes and fires the timers that are due.
    A tuple is (entity_id, state) or (entity_id, state, attributes). The last step
    must leave no run waiting, because the test then blocks until runs finish.
    """

    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        for item in items:
            if isinstance(item, tuple):
                entity_id, state, *rest = item
                hass.states.async_set(entity_id, state, rest[0] if rest else {})
            else:
                freezer.tick(timedelta(minutes=item))
                async_fire_time_changed(hass, dt_util.utcnow())
            await settle()

    return fire


def on_weekday(weekday: str, hour: int) -> Callable:
    """Move to the next `hour` o'clock that falls on `weekday`, and fire it."""

    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        now = dt_util.now()
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        target += timedelta(days=(WEEKDAYS[weekday] - target.weekday()) % 7)
        if target <= now:
            target += timedelta(days=7)
        freezer.move_to(target)
        async_fire_time_changed(hass, target)

    return fire


def _left_on_targets(expected: list[str]) -> Callable[..., None]:
    def check(state: Any, questions: dict[str, Any]) -> None:
        assert sorted(e["entity_id"] for e in state["entities"]) == sorted(expected)
        # The blueprint must not tell Jev the room is empty. The sensors say it.
        assert "trust" not in str(questions["answer"].instructions)

    return check


def _laundry_watts(_state: Any, questions: dict[str, Any]) -> None:
    text = str(questions["answer"].instructions)
    assert "under 3 W" in text
    assert "about 1800 W" in text


def _price_unknown(state: Any, _questions: Any) -> None:
    note = state["note"]
    assert "price unknown" in note["electricity"]
    assert "well" not in note["electricity"]
    assert "not reporting" in note["solar"]


def _solar_kw(state: Any, questions: dict[str, Any]) -> None:
    # 2 kW is 2000 W, above the 1500 W default. Read as 2 W it would be too little.
    assert "producing enough" in state["note"]["solar"]
    assert "none" in questions["reason"].criteria


def _appliance_default_name(_state: Any, questions: dict[str, Any]) -> None:
    assert "Was the appliance left on" in str(questions["answer"].instructions)


def _fridge_causes(_state: Any, questions: dict[str, Any]) -> None:
    assert set(questions["cause"].criteria) == {*FRIDGE_CAUSES, "other"}
    assert "This is a freezer" in str(questions["cause"].instructions)
    assert "-20 to -16" in str(questions["cause"].instructions)


def _battery_silent(state: Any, _questions: Any) -> None:
    assert "Not reporting, possibly flat: sensor.smoke_alarm_battery." in state


LAUNDRY = {
    "power_sensor": "sensor.washer_power",
    "door_sensor": [],
    "hold": NO_WAIT,
    "cooldown": NO_WAIT,
    "threshold": 0.7,
    "reminder_actions": run("yes", p="{{ probability }}"),
}

DISHWASHER = {
    "ready_entity": "binary_sensor.dishwasher_ready",
    "price_sensor": "sensor.energy_price",
    "avg_price_sensor": "sensor.energy_price_avg",
    "solar_sensor": "",
    "hold": NO_WAIT,
    "window_start": "00:00:00",
    "window_end": "23:59:59",
    "cooldown": NO_WAIT,
    "threshold": 0.7,
    "start_actions": run("yes", p="{{ probability }}", r="{{ reason }}"),
}

DISHWASHER_REASON = ChoiceAnswer(
    choice="price",
    probabilities={
        "price": 0.8,
        "solar": 0.05,
        "people": 0.05,
        "time": 0.05,
        "none": 0.05,
    },
    confidence=0.8,
)

APPLIANCE = {
    "power_sensor": "sensor.oven_power",
    "presence_sensors": [],
    "idle_watts": 5,
    "hold": {"minutes": 15},
    "recheck": {"minutes": 30},
    "cooldown": NO_WAIT,
    "appliance_name": "the oven",
    "threshold": 0.6,
    "mistake_actions": run("yes", p="{{ probability }}", m="{{ minutes_on }}"),
}

# On at minute 0, and the reading moves at minute 10, inside the 15 minute hold.
# minutes_on counts from minute 0, not from the last change in the reading.
OVEN_ON = (
    ("sensor.oven_power", "1500", {"unit_of_measurement": "W"}),
    10,
    ("sensor.oven_power", "1510", {"unit_of_measurement": "W"}),
    5,
)
# Switching it off ends the run, which the test needs before it can finish.
OVEN_OFF = ("sensor.oven_power", "0", {"unit_of_measurement": "W"})

LEFT_ON = {
    "lights": ["light.living_room"],
    "media_players": [],
    "fans": [],
    "occupancy_sensors": ["binary_sensor.living_room_occupancy"],
    "hold": NO_WAIT,
    "cooldown": NO_WAIT,
    "threshold": 0.6,
    "turn_off_actions": run("yes", p="{{ probability }}", l="{{ left_on }}"),
}

FRIDGE = {
    "temp_sensor": "sensor.fridge_temp",
    "door_sensor": "",
    "appliance_type": "fridge",
    "hold": NO_WAIT,
    "cooldown": NO_WAIT,
    "urgent_threshold": 2.5,
    "urgent_actions": run(
        "yes", cause="{{ cause }}", u="{{ urgency }}", lvl="{{ level }}"
    ),
    "worth_a_look_actions": run(
        "no", cause="{{ cause }}", u="{{ urgency }}", lvl="{{ level }}"
    ),
}

FRIDGE_CAUSES = ("door", "restocked", "defrost", "power_cut", "failing", "sensor_fault")


def _fridge_cause(choice: str) -> ChoiceAnswer:
    rest = (1 - 0.7) / len(FRIDGE_CAUSES)
    return ChoiceAnswer(
        choice=choice,
        probabilities={c: 0.7 if c == choice else rest for c in FRIDGE_CAUSES},
        confidence=0.7,
    )


def _fridge_urgency(score: float) -> ScoreAnswer:
    return ScoreAnswer(
        score=score,
        legend=FRIDGE_LEGEND,
        probabilities={str(round(score)): 0.6},
        confidence=0.7,
    )


FRIDGE_URGENT = {"cause": _fridge_cause("door"), "urgency": _fridge_urgency(2.8)}

BATTERY = {
    "battery_sensors": ["sensor.battery1", "sensor.battery2"],
    "low_percent": 20,
    "weekday": ["mon"],
    "time": "09:00:00",
    "protects_threshold": 0.6,
    "actions": run(
        "yes",
        lb="{{ low_batteries }}",
        nr="{{ not_reporting }}",
        u="{{ urgency }}",
        lvl="{{ level }}",
        pp="{{ protects_people }}",
    ),
}

BATTERY_ANSWERS = {
    "protects": NoulAnswer(noul=0.9),
    "urgency": ScoreAnswer(
        score=2.0, legend=BATTERY_LEGEND, probabilities={"2": 0.6}, confidence=0.7
    ),
}


CASES: dict[str, Case] = {
    "laundry_forgotten": Case(
        states={"sensor.washer_power": ("300", {"unit_of_measurement": "W"})},
        inputs={**LAUNDRY, "idle_watts": 3, "running_watts": 1800},
        fire=change("sensor.washer_power", "2", unit_of_measurement="W"),
        answers={"answer": NoulAnswer(noul=0.85)},
        expect={"yes": [{"p": 0.85}]},
        check_request=_laundry_watts,
    ),
    "laundry_forgotten:below_threshold": Case(
        states={"sensor.washer_power": ("300", {"unit_of_measurement": "W"})},
        inputs=LAUNDRY,
        fire=change("sensor.washer_power", "2", unit_of_measurement="W"),
        answers={"answer": NoulAnswer(noul=0.2)},
        expect={},
    ),
    # Standby jitter, and an attribute-only change, while the machine stays idle.
    # Neither crosses below idle watts, so neither asks.
    "laundry_forgotten:standby_jitter": Case(
        states={"sensor.washer_power": ("0.5", {"unit_of_measurement": "W"})},
        inputs=LAUNDRY,
        fire=steps(
            ("sensor.washer_power", "0.6", {"unit_of_measurement": "W"}),
            30,
            ("sensor.washer_power", "0.6", {"unit_of_measurement": "W", "x": 1}),
            30,
        ),
        answers={"answer": NoulAnswer(noul=0.85)},
        expect={},
        asks=0,
    ),
    # A yes runs the start actions once. The 61 minute tick crosses a 15 minute
    # check with ready still on, and that must not ask again. Ready going off
    # ends the run.
    "dishwasher_good_moment": Case(
        states={
            "sensor.energy_price": ("0.10", {}),
            "sensor.energy_price_avg": ("0.30", {}),
        },
        inputs=DISHWASHER,
        fire=steps(
            ("binary_sensor.dishwasher_ready", "on"),
            61,
            ("binary_sensor.dishwasher_ready", "off"),
        ),
        answers={"good_moment": NoulAnswer(noul=0.9), "reason": DISHWASHER_REASON},
        expect={"yes": [{"p": 0.9, "r": "price"}]},
    ),
    # A no keeps asking while ready stays on, once per cooldown.
    "dishwasher_good_moment:no_asks_again": Case(
        states={
            "sensor.energy_price": ("0.40", {}),
            "sensor.energy_price_avg": ("0.30", {}),
        },
        inputs=DISHWASHER,
        fire=change("binary_sensor.dishwasher_ready", "on"),
        answers={"good_moment": NoulAnswer(noul=0.2), "reason": DISHWASHER_REASON},
        asks=2,
        expect={},
    ),
    "dishwasher_good_moment:price_unknown": Case(
        states={
            "sensor.energy_price": ("unavailable", {}),
            "sensor.energy_price_avg": ("0.30", {}),
            "sensor.solar_power": ("unavailable", {}),
        },
        inputs={**DISHWASHER, "solar_sensor": "sensor.solar_power"},
        fire=change("binary_sensor.dishwasher_ready", "on"),
        answers={"good_moment": NoulAnswer(noul=0.2), "reason": DISHWASHER_REASON},
        # The 61 minute tick asks a second time, because the answer was no.
        asks=2,
        expect={},
        check_request=_price_unknown,
    ),
    "dishwasher_good_moment:solar_kw": Case(
        states={
            "sensor.energy_price": ("0.30", {}),
            "sensor.energy_price_avg": ("0.30", {}),
            "sensor.solar_power": ("2", {"unit_of_measurement": "kW"}),
        },
        inputs={**DISHWASHER, "solar_sensor": "sensor.solar_power"},
        fire=change("binary_sensor.dishwasher_ready", "on"),
        answers={"good_moment": NoulAnswer(noul=0.2), "reason": DISHWASHER_REASON},
        asks=2,
        expect={},
        check_request=_solar_kw,
    ),
    "appliance_left_on": Case(
        states={"sensor.oven_power": ("0", {"unit_of_measurement": "W"})},
        inputs=APPLIANCE,
        fire=steps(*OVEN_ON, OVEN_OFF),
        answers={"answer": NoulAnswer(noul=0.75)},
        expect={"yes": [{"p": 0.75, "m": 15.0}]},
    ),
    # Still on 30 minutes after the first question, so it asks again.
    "appliance_left_on:recheck": Case(
        states={"sensor.oven_power": ("0", {"unit_of_measurement": "W"})},
        inputs=APPLIANCE,
        fire=steps(*OVEN_ON, 30, OVEN_OFF),
        answers={"answer": NoulAnswer(noul=0.75)},
        asks=2,
        expect={"yes": [{"p": 0.75, "m": 15.0}, {"p": 0.75, "m": 45.0}]},
    ),
    # Switched off after the first question, so the recheck does not ask. The
    # name is left at its default.
    "appliance_left_on:switched_off": Case(
        states={"sensor.oven_power": ("0", {"unit_of_measurement": "W"})},
        inputs={k: v for k, v in APPLIANCE.items() if k != "appliance_name"},
        fire=steps(*OVEN_ON, OVEN_OFF, 30),
        answers={"answer": NoulAnswer(noul=0.75)},
        expect={"yes": [{"p": 0.75, "m": 15.0}]},
        check_request=_appliance_default_name,
    ),
    "left_on_in_an_empty_room": Case(
        states={
            "light.living_room": ("on", {}),
            "binary_sensor.living_room_occupancy": ("on", {}),
        },
        inputs=LEFT_ON,
        fire=change("binary_sensor.living_room_occupancy", "off"),
        answers={"answer": NoulAnswer(noul=0.85)},
        expect={"yes": [{"p": 0.85, "l": ["light.living_room"]}]},
        check_request=_left_on_targets(
            ["light.living_room", "binary_sensor.living_room_occupancy"]
        ),
    ),
    # A paused TV is still on. Only off, standby, unavailable and unknown are not.
    "left_on_in_an_empty_room:paused_tv": Case(
        states={
            "light.living_room": ("off", {}),
            "media_player.tv": ("paused", {}),
            "media_player.speaker": ("standby", {}),
            "binary_sensor.living_room_occupancy": ("on", {}),
        },
        inputs={
            **LEFT_ON,
            "media_players": ["media_player.tv", "media_player.speaker"],
        },
        fire=change("binary_sensor.living_room_occupancy", "off"),
        answers={"answer": NoulAnswer(noul=0.85)},
        expect={"yes": [{"p": 0.85, "l": ["media_player.tv"]}]},
    ),
    "left_on_in_an_empty_room:nothing_on": Case(
        states={
            "light.living_room": ("off", {}),
            "binary_sensor.living_room_occupancy": ("on", {}),
        },
        inputs={**LEFT_ON, "turn_off_actions": run("yes", p="{{ probability }}")},
        fire=change("binary_sensor.living_room_occupancy", "off"),
        answers={"answer": NoulAnswer(noul=0.85)},
        expect={},
        asks=0,
    ),
    "fridge_freezer_watch:urgent": Case(
        states={"sensor.fridge_temp": ("4", {})},
        inputs=FRIDGE,
        fire=change("sensor.fridge_temp", "12"),
        answers=FRIDGE_URGENT,
        expect={"yes": [{"cause": "door", "u": 2.8, "lvl": "Act now, food is at risk"}]},
    ),
    "fridge_freezer_watch:worth_a_look": Case(
        states={"sensor.fridge_temp": ("4", {})},
        inputs=FRIDGE,
        fire=change("sensor.fridge_temp", "9"),
        answers={"cause": _fridge_cause("restocked"), "urgency": _fridge_urgency(1.0)},
        expect={
            "no": [{"cause": "restocked", "u": 1.0, "lvl": "Worth a look this week"}]
        },
    ),
    # -10 is far too warm for a freezer, and far below the fridge limit of 8.
    "fridge_freezer_watch:freezer": Case(
        states={"sensor.freezer_temp": ("-18", {})},
        inputs={
            **FRIDGE,
            "temp_sensor": "sensor.freezer_temp",
            "appliance_type": "freezer",
        },
        fire=change("sensor.freezer_temp", "-10"),
        answers=FRIDGE_URGENT,
        expect={"yes": [{"cause": "door", "u": 2.8, "lvl": "Act now, food is at risk"}]},
        check_request=_fridge_causes,
    ),
    "fridge_freezer_watch:freezer_cold_enough": Case(
        states={"sensor.freezer_temp": ("-18", {})},
        inputs={
            **FRIDGE,
            "temp_sensor": "sensor.freezer_temp",
            "appliance_type": "freezer",
        },
        fire=change("sensor.freezer_temp", "-14"),
        answers=FRIDGE_URGENT,
        expect={},
        asks=0,
    ),
    "fridge_freezer_watch:sensor_unavailable": Case(
        states={"sensor.fridge_temp": ("4", {})},
        inputs=FRIDGE,
        fire=change("sensor.fridge_temp", "unavailable"),
        answers=FRIDGE_URGENT,
        expect={},
        asks=0,
    ),
    "battery_triage": Case(
        states={
            "sensor.battery1": ("15", {}),
            "sensor.battery2": ("80", {}),
        },
        inputs=BATTERY,
        fire=on_weekday("mon", 9),
        answers=BATTERY_ANSWERS,
        expect={
            "yes": [
                {
                    "lb": "sensor.battery1: 15%",
                    "nr": "",
                    "u": 2.0,
                    "lvl": "Within the next few days",
                    "pp": True,
                }
            ]
        },
    ),
    # A flat smoke alarm battery that reports unavailable is the urgent case.
    "battery_triage:not_reporting": Case(
        states={
            "sensor.battery1": ("80", {}),
            "sensor.smoke_alarm_battery": ("unavailable", {}),
        },
        inputs={
            **BATTERY,
            "battery_sensors": ["sensor.battery1", "sensor.smoke_alarm_battery"],
        },
        fire=on_weekday("mon", 9),
        answers=BATTERY_ANSWERS,
        expect={
            "yes": [
                {
                    "lb": "sensor.smoke_alarm_battery: not reporting",
                    "nr": "sensor.smoke_alarm_battery",
                    "u": 2.0,
                    "lvl": "Within the next few days",
                    "pp": True,
                }
            ]
        },
        check_request=_battery_silent,
    ),
    # Monday only, and it is Tuesday at 09:00, so nothing is asked.
    "battery_triage:other_weekday": Case(
        states={"sensor.battery1": ("15", {}), "sensor.battery2": ("80", {})},
        inputs=BATTERY,
        fire=on_weekday("tue", 9),
        answers=BATTERY_ANSWERS,
        expect={},
        asks=0,
    ),
    "battery_triage:none_low": Case(
        states={
            "sensor.battery1": ("80", {}),
            "sensor.battery2": ("90", {}),
        },
        inputs={**BATTERY, "weekday": ALL_WEEKDAYS, "actions": run("yes")},
        fire=at(9, 0),
        answers=BATTERY_ANSWERS,
        expect={},
        asks=0,
    ),
}
