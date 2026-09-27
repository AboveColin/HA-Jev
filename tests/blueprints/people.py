"""Cases for the blueprints about people coming and going, the door and the calendar."""

import asyncio
import datetime as dt
import pathlib
from collections.abc import Callable
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.components.calendar.const import DATA_COMPONENT
from homeassistant.components.trace.const import DATA_TRACE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import selector
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import load_yaml
from jevclient import ChoiceAnswer, NoulAnswer, ScoreAnswer
from pytest_homeassistant_custom_component.common import (
    MockPlatform,
    async_fire_time_changed,
    mock_platform,
)

from .kit import NO_WAIT, Case, at, change, run

BLUEPRINTS = pathlib.Path(__file__).parents[2] / "blueprints" / "automation" / "jev"

URGENCY_LEVELS_HOME = [
    "Nothing to worry about",
    "Worth a look when someone is back",
    "Should be handled today",
    "Needs handling right now",
]
URGENCY_LEVELS_NIGHT = [
    "Fine until morning",
    "Worth a look before bed",
    "Should be handled tonight",
    "Needs handling right now",
]
URGENCY_LEVELS_AWAY = [
    "Nothing to worry about",
    "Worth keeping an eye on",
    "Worth checking on soon",
    "Needs checking on right now",
]


def _legend(levels: list[str]) -> dict[str, str]:
    return {str(i): level for i, level in enumerate(levels)}


CASES: dict[str, Case] = {
    "leaving_home_check:handle": Case(
        states={
            "zone.home": ("1", {}),
            "light.living_room": ("on", {"friendly_name": "Living room light"}),
        },
        inputs={
            "zone": "zone.home",
            "lights": ["light.living_room"],
            "windows": [],
            "appliances": [],
            "covers": [],
            "media": [],
            "hold": NO_WAIT,
            "cooldown": NO_WAIT,
            "threshold": 0.6,
            "handle_actions": run(
                "yes",
                p="{{ probability }}",
                left="{{ left_on }}",
                urg="{{ urgency }}",
                lvl="{{ level }}",
            ),
            "clear_actions": run("no", p="{{ probability }}"),
        },
        fire=change("zone.home", "0"),
        answers={
            "needs_handling": NoulAnswer(noul=0.83),
            "urgency": ScoreAnswer(
                score=2.0,
                legend=_legend(URGENCY_LEVELS_HOME),
                probabilities={"0": 0.05, "1": 0.1, "2": 0.6, "3": 0.25},
                confidence=0.8,
            ),
        },
        expect={
            "yes": [
                {
                    "p": 0.83,
                    "left": "Living room light",
                    "urg": 2.0,
                    "lvl": "Should be handled today",
                }
            ]
        },
    ),
    "leaving_home_check:clear": Case(
        states={
            "zone.home": ("1", {}),
            "light.living_room": ("on", {"friendly_name": "Living room light"}),
        },
        inputs={
            "zone": "zone.home",
            "lights": ["light.living_room"],
            "windows": [],
            "appliances": [],
            "covers": [],
            "media": [],
            "hold": NO_WAIT,
            "cooldown": NO_WAIT,
            "threshold": 0.6,
            "handle_actions": run("yes", p="{{ probability }}"),
            "clear_actions": run("no", p="{{ probability }}"),
        },
        fire=change("zone.home", "0"),
        answers={
            "needs_handling": NoulAnswer(noul=0.2),
            "urgency": ScoreAnswer(
                score=0.0,
                legend=_legend(URGENCY_LEVELS_HOME),
                probabilities={"0": 0.7, "1": 0.2, "2": 0.05, "3": 0.05},
                confidence=0.75,
            ),
        },
        expect={"no": [{"p": 0.2}]},
    ),
    "bedtime_check:handle": Case(
        states={
            "binary_sensor.front_door": ("on", {"friendly_name": "Front door"}),
        },
        inputs={
            "doors": ["binary_sensor.front_door"],
            "windows": [],
            "oven": [],
            "lights": [],
            "locks": [],
            "garage_doors": [],
            "bedtime": "23:00:00",
            "trigger_entities": [],
            "trigger_hold": NO_WAIT,
            "cooldown": NO_WAIT,
            "threshold": 0.6,
            "handle_actions": run(
                "yes",
                p="{{ probability }}",
                left="{{ left_on }}",
                urg="{{ urgency }}",
                lvl="{{ level }}",
            ),
            "clear_actions": run("no", p="{{ probability }}"),
        },
        fire=at(23, 0),
        answers={
            "needs_handling": NoulAnswer(noul=0.9),
            "urgency": ScoreAnswer(
                score=3.0,
                legend=_legend(URGENCY_LEVELS_NIGHT),
                probabilities={"0": 0.02, "1": 0.03, "2": 0.15, "3": 0.8},
                confidence=0.85,
            ),
        },
        expect={
            "yes": [
                {
                    "p": 0.9,
                    "left": "Front door",
                    "urg": 3.0,
                    "lvl": "Needs handling right now",
                }
            ]
        },
    ),
    "bedtime_check:clear": Case(
        states={
            "binary_sensor.front_door": ("on", {"friendly_name": "Front door"}),
        },
        inputs={
            "doors": ["binary_sensor.front_door"],
            "windows": [],
            "oven": [],
            "lights": [],
            "locks": [],
            "garage_doors": [],
            "bedtime": "23:00:00",
            "trigger_entities": [],
            "trigger_hold": NO_WAIT,
            "cooldown": NO_WAIT,
            "threshold": 0.6,
            "handle_actions": run("yes", p="{{ probability }}"),
            "clear_actions": run("no", p="{{ probability }}"),
        },
        fire=at(23, 0),
        answers={
            "needs_handling": NoulAnswer(noul=0.15),
            "urgency": ScoreAnswer(
                score=0.0,
                legend=_legend(URGENCY_LEVELS_NIGHT),
                probabilities={"0": 0.8, "1": 0.1, "2": 0.05, "3": 0.05},
                confidence=0.7,
            ),
        },
        expect={"no": [{"p": 0.15}]},
    ),
}


def _doorbell_inputs(**branch_actions: list) -> dict:
    base = {
        "transcript_entity": "input_text.doorbell_transcript",
        "transcript_attribute": "",
        "hold": NO_WAIT,
        "cooldown": NO_WAIT,
        "household": "Sam and Alex live here, and they order parcels often.",
        "min_confidence": 0.6,
        "delivery_actions": [],
        "neighbour_actions": [],
        "sales_actions": [],
        "service_actions": [],
        "other_actions": [],
        "unsure_actions": [],
    }
    base.update(branch_actions)
    return base


def _doorbell_answer(choice: str, confidence: float) -> dict:
    probabilities = {
        opt: 0.04 for opt in ("delivery", "neighbour", "sales", "service", "other")
    }
    probabilities[choice] = round(1 - 0.04 * 4, 2)
    return {
        "answer": ChoiceAnswer(
            choice=choice, probabilities=probabilities, confidence=confidence
        )
    }


CASES["doorbell_caller:delivery"] = Case(
    states={"input_text.doorbell_transcript": ("", {})},
    inputs=_doorbell_inputs(
        delivery_actions=run("yes", who="{{ caller }}", sure="{{ confidence }}")
    ),
    fire=change(
        "input_text.doorbell_transcript", "Package for number 31, nobody answering"
    ),
    answers=_doorbell_answer("delivery", 0.84),
    expect={"yes": [{"who": "delivery", "sure": 0.84}]},
)
CASES["doorbell_caller:neighbour"] = Case(
    states={"input_text.doorbell_transcript": ("", {})},
    inputs=_doorbell_inputs(neighbour_actions=run("yes", who="{{ caller }}")),
    fire=change("input_text.doorbell_transcript", "Hi, it's Mieke from next door"),
    answers=_doorbell_answer("neighbour", 0.79),
    expect={"yes": [{"who": "neighbour"}]},
)
CASES["doorbell_caller:sales"] = Case(
    states={"input_text.doorbell_transcript": ("", {})},
    inputs=_doorbell_inputs(sales_actions=run("yes", who="{{ caller }}")),
    fire=change(
        "input_text.doorbell_transcript",
        "Good afternoon, do you have a minute to talk about your energy contract",
    ),
    answers=_doorbell_answer("sales", 0.88),
    expect={"yes": [{"who": "sales"}]},
)
CASES["doorbell_caller:service"] = Case(
    states={"input_text.doorbell_transcript": ("", {})},
    inputs=_doorbell_inputs(service_actions=run("yes", who="{{ caller }}")),
    fire=change(
        "input_text.doorbell_transcript", "Meter reader, here for the annual reading"
    ),
    answers=_doorbell_answer("service", 0.81),
    expect={"yes": [{"who": "service"}]},
)
CASES["doorbell_caller:other"] = Case(
    states={"input_text.doorbell_transcript": ("", {})},
    inputs=_doorbell_inputs(other_actions=run("yes", who="{{ caller }}")),
    fire=change(
        "input_text.doorbell_transcript", "Something unrelated to any of the above"
    ),
    answers=_doorbell_answer("other", 0.7),
    expect={"yes": [{"who": "other"}]},
)
CASES["doorbell_caller:unsure"] = Case(
    states={"input_text.doorbell_transcript": ("", {})},
    inputs=_doorbell_inputs(
        unsure_actions=run("no", who="{{ caller }}", sure="{{ confidence }}")
    ),
    fire=change("input_text.doorbell_transcript", "Mumbled, hard to make out"),
    answers=_doorbell_answer("sales", 0.35),
    expect={"no": [{"who": "sales", "sure": 0.35}]},
)


def _parcel_inputs(**branch_actions: list) -> dict:
    base = {
        "message_entity": "input_text.email_text",
        "message_attribute": "",
        "hold": NO_WAIT,
        "cooldown": NO_WAIT,
        "background": "",
        "arriving_actions": [],
        "delivered_actions": [],
        "needs_action_actions": [],
    }
    base.update(branch_actions)
    return base


CASES["parcel_today:arriving_today"] = Case(
    states={"input_text.email_text": ("", {})},
    inputs=_parcel_inputs(
        arriving_actions=run(
            "yes", s="{{ status }}", t="{{ text }}", n="{{ needs_someone }}"
        )
    ),
    fire=change("input_text.email_text", "Your parcel is out for delivery today"),
    answers={
        "status": ChoiceAnswer(
            choice="arriving_today",
            probabilities={
                "arriving_today": 0.85,
                "delivered": 0.05,
                "needs_action": 0.05,
                "not_about_parcel": 0.05,
            },
            confidence=0.85,
        ),
        "needs_someone": NoulAnswer(noul=0.75),
    },
    expect={
        "yes": [
            {
                "s": "arriving_today",
                "t": "Your parcel is out for delivery today",
                "n": 0.75,
            }
        ]
    },
)
CASES["parcel_today:delivered"] = Case(
    states={"input_text.email_text": ("", {})},
    inputs=_parcel_inputs(delivered_actions=run("yes", s="{{ status }}")),
    fire=change("input_text.email_text", "Your parcel was delivered to the front door"),
    answers={
        "status": ChoiceAnswer(
            choice="delivered",
            probabilities={
                "arriving_today": 0.05,
                "delivered": 0.85,
                "needs_action": 0.05,
                "not_about_parcel": 0.05,
            },
            confidence=0.9,
        ),
        "needs_someone": NoulAnswer(noul=0.1),
    },
    expect={"yes": [{"s": "delivered"}]},
)
CASES["parcel_today:needs_action"] = Case(
    states={"input_text.email_text": ("", {})},
    inputs=_parcel_inputs(
        needs_action_actions=run("no", s="{{ status }}", n="{{ needs_someone }}")
    ),
    fire=change(
        "input_text.email_text", "We missed you, your parcel is held at the depot"
    ),
    answers={
        "status": ChoiceAnswer(
            choice="needs_action",
            probabilities={
                "arriving_today": 0.05,
                "delivered": 0.05,
                "needs_action": 0.85,
                "not_about_parcel": 0.05,
            },
            confidence=0.8,
        ),
        "needs_someone": NoulAnswer(noul=0.65),
    },
    expect={"no": [{"s": "needs_action", "n": 0.65}]},
)

CASES["motion_while_away:urgent"] = Case(
    states={
        "binary_sensor.hallway_motion": ("off", {"friendly_name": "Hallway motion"}),
        "person.alex": ("not_home", {}),
    },
    inputs={
        "motion_sensors": ["binary_sensor.hallway_motion"],
        "door_sensors": [],
        "persons": ["person.alex"],
        "hold": NO_WAIT,
        "cooldown": NO_WAIT,
        "background": "",
        "urgent_threshold": 2,
        "urgent_actions": run(
            "yes",
            e="{{ explanation }}",
            u="{{ urgency }}",
            lvl="{{ level }}",
            c="{{ confidence }}",
        ),
        "explained_actions": run("no", e="{{ explanation }}"),
    },
    fire=change("binary_sensor.hallway_motion", "on"),
    answers={
        "explanation": ChoiceAnswer(
            choice="unexplained",
            probabilities={
                "resident_arriving": 0.1,
                "pet": 0.1,
                "expected_visitor": 0.05,
                "unexplained": 0.75,
            },
            confidence=0.75,
        ),
        "urgency": ScoreAnswer(
            score=3.0,
            legend=_legend(URGENCY_LEVELS_AWAY),
            probabilities={"0": 0.02, "1": 0.03, "2": 0.15, "3": 0.8},
            confidence=0.8,
        ),
    },
    expect={
        "yes": [
            {
                "e": "unexplained",
                "u": 3.0,
                "lvl": "Needs checking on right now",
                "c": 0.75,
            }
        ]
    },
)
CASES["motion_while_away:explained"] = Case(
    states={
        "binary_sensor.hallway_motion": ("off", {"friendly_name": "Hallway motion"}),
        "person.alex": ("not_home", {}),
    },
    inputs={
        "motion_sensors": ["binary_sensor.hallway_motion"],
        "door_sensors": [],
        "persons": ["person.alex"],
        "hold": NO_WAIT,
        "cooldown": NO_WAIT,
        "background": "The cat sets off the hallway sensor most evenings.",
        "urgent_threshold": 2,
        "urgent_actions": run("yes", e="{{ explanation }}"),
        "explained_actions": run("no", e="{{ explanation }}", u="{{ urgency }}"),
    },
    fire=change("binary_sensor.hallway_motion", "on"),
    answers={
        "explanation": ChoiceAnswer(
            choice="pet",
            probabilities={
                "resident_arriving": 0.05,
                "pet": 0.85,
                "expected_visitor": 0.05,
                "unexplained": 0.05,
            },
            confidence=0.88,
        ),
        "urgency": ScoreAnswer(
            score=0.0,
            legend=_legend(URGENCY_LEVELS_AWAY),
            probabilities={"0": 0.8, "1": 0.1, "2": 0.05, "3": 0.05},
            confidence=0.7,
        ),
    },
    expect={"no": [{"e": "pet", "u": 0.0}]},
)


class _JevTestCalendarEntity(CalendarEntity):
    """A minimal real calendar entity.

    The calendar.event_started trigger looks up a real registered
    CalendarEntity when it attaches. A bare state set with
    hass.states.async_set is not enough, so this test registers one
    through a mock platform instead.
    """

    _attr_name = "Jev test calendar"
    _attr_unique_id = "jev_test_calendar"

    def __init__(self, events: list[CalendarEvent]) -> None:
        self._events = events

    @property
    def event(self) -> CalendarEvent | None:
        return self._events[0]

    async def async_get_events(
        self, hass: HomeAssistant, start_date: dt.datetime, end_date: dt.datetime
    ) -> list[CalendarEvent]:
        return [
            e for e in self._events if start_date <= e.start_datetime_local < end_date
        ]


def _slow_jev(hass: HomeAssistant) -> None:
    """Make the mocked Jev yield to the loop once before it answers.

    The mock answers without yielding, so a run finishes before the next trigger
    is dispatched and "mode: single" never gets the chance to drop one. A real
    call waits on the network, and this wait stands in for it.
    """
    client = hass.config_entries.async_loaded_entries("jev")[0].runtime_data.client

    async def answer(*_args: Any) -> Any:
        await asyncio.sleep(0)
        return client.ask.return_value

    client.ask.side_effect = answer


def _calendar_fire(*summaries: str, slow: bool = False) -> Callable:
    """A fire() for calendar_prep with one event per summary, all at the same start."""

    async def fire(hass: HomeAssistant, freezer) -> None:
        if slow:
            _slow_jev(hass)
        await _calendar_prep_fire(hass, freezer, summaries or ("Dentist appointment",))

    return fire


async def _calendar_prep_fire(
    hass: HomeAssistant, freezer, summaries: tuple[str, ...] = ("Dentist appointment",)
) -> None:
    """Register a real calendar entity, then let the native trigger catch its event.

    The automation is set up before this runs, so its calendar trigger
    already tried to attach to an entity that did not exist yet, and that
    first attempt gave up quietly. Registering the entity through a real
    platform, rather than a bare state, adds it to the entity registry.
    The trigger already listens for entity registry updates to redo its
    target lookup, so that registration alone makes it pick up the real
    calendar and start listening for its events, with no need to turn the
    automation off and back on.
    """
    now = dt_util.now()
    event_start = now + dt.timedelta(minutes=12)
    events = [
        CalendarEvent(
            start=event_start,
            end=event_start + dt.timedelta(hours=1),
            summary=summary,
            description="Yearly checkup, bring the insurance card",
            location="Main street dental",
        )
        for summary in summaries
    ]

    async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
        async_add_entities([_JevTestCalendarEntity(events)])

    mock_platform(
        hass,
        "jev_test_cal.calendar",
        MockPlatform(async_setup_platform=async_setup_platform),
    )
    assert await async_setup_component(
        hass, "calendar", {"calendar": [{"platform": "jev_test_cal"}]}
    )
    await hass.async_block_till_done()

    trigger_time = event_start - dt.timedelta(minutes=10)
    freezer.move_to(trigger_time)
    async_fire_time_changed(hass, trigger_time)
    await hass.async_block_till_done()

    # The calendar entity itself schedules alarms for the event's own start
    # and end, on top of the trigger's scheduling. Those belong to the
    # entity, not to the automation, so turning the automation off never
    # cancels them; removing the entity does. Without this the test harness
    # fails on a lingering timer that has nothing left to do.
    entity = hass.data[DATA_COMPONENT].get_entity("calendar.jev_test_calendar")
    if entity is not None:
        await entity.async_remove(force_remove=True)


CASES["calendar_prep:handle"] = Case(
    states={},
    inputs={
        "calendar_entity": "calendar.jev_test_calendar",
        "offset": {"minutes": 10},
        "background": "",
        "prep_actions": run(
            "yes", s="{{ summary }}", p="{{ probability }}", w="{{ what }}"
        ),
        "clear_actions": run("no", p="{{ probability }}"),
    },
    fire=_calendar_prep_fire,
    answers={
        "needs_prep": NoulAnswer(noul=0.8),
        "what": ChoiceAnswer(
            choice="bring",
            probabilities={
                "bring": 0.7,
                "leave_early": 0.1,
                "buy_or_book": 0.1,
                "nothing": 0.1,
            },
            confidence=0.75,
        ),
    },
    expect={
        "yes": [{"s": "Dentist appointment", "p": 0.8, "w": "bring"}],
    },
)

CASES["calendar_prep:clear"] = Case(
    states={},
    inputs={
        "calendar_entity": "calendar.jev_test_calendar",
        "offset": {"minutes": 10},
        "background": "",
        "prep_actions": run(
            "yes", s="{{ summary }}", p="{{ probability }}", w="{{ what }}"
        ),
        "clear_actions": run("no", p="{{ probability }}"),
    },
    fire=_calendar_prep_fire,
    answers={
        "needs_prep": NoulAnswer(noul=0.1),
        "what": ChoiceAnswer(
            choice="nothing",
            probabilities={
                "bring": 0.05,
                "leave_early": 0.05,
                "buy_or_book": 0.05,
                "nothing": 0.85,
            },
            confidence=0.9,
        ),
    },
    expect={
        "no": [{"p": 0.1}],
    },
)


# ---------------------------------------------------------------------------
# Cases for the review fixes. Each one fails with the fix reverted.


def _change_at(hour: int, minute: int, entity_id: str, state: str) -> Callable:
    """Move the clock back to a local time of day, then change one entity.

    The clock moves back, never forward, so it passes no scheduled timer and a
    time trigger such as the bedtime cannot run on the way. Every other state is
    set again at the new time, since a state that changed in the future is one
    the integration refuses to describe. A zero hold still waits for the next
    timer tick, so the clock then moves on one second.
    """

    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        now = dt_util.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target > now:
            target -= dt.timedelta(days=1)
        freezer.move_to(target)
        for old in hass.states.async_all():
            if old.domain in ("automation", "zone") or old.entity_id == entity_id:
                continue
            hass.states.async_remove(old.entity_id)
            hass.states.async_set(old.entity_id, old.state, dict(old.attributes))
        hass.states.async_remove(entity_id)
        hass.states.async_set(entity_id, "off", {})
        await hass.async_block_till_done()
        hass.states.async_set(entity_id, state, {})
        await hass.async_block_till_done()
        freezer.tick(dt.timedelta(seconds=1))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

    return fire


async def _settle(hass: HomeAssistant) -> None:
    """Let pending callbacks run without waiting for a run that sits in a delay.

    async_block_till_done waits for every automation run, and a run in its
    cooldown delay only ends when the clock passes it.
    """
    for _ in range(20):
        await asyncio.sleep(0)


def _pulses(entity_id: str, count: int, on_for: int, every: int) -> Callable:
    """A motion sensor that reports "on" for on_for seconds, every `every` seconds.

    Afterwards the clock moves on an hour, past any cooldown a case leaves at its
    default, so the harness can wait for the run to finish.
    """

    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        for _ in range(count):
            for state, wait in (("on", on_for), ("off", every - on_for)):
                hass.states.async_set(entity_id, state, {})
                await _settle(hass)
                freezer.tick(dt.timedelta(seconds=wait))
                async_fire_time_changed(hass, dt_util.utcnow())
                await _settle(hass)
        freezer.tick(dt.timedelta(hours=1))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

    return fire


def _change_without_a_run(entity_id: str, state: str, **attributes: Any) -> Callable:
    """Change an entity, then fail if the automation's trigger fired at all.

    A run that a condition stops never asks Jev either, so the ask count alone
    cannot tell a trigger that ignored the change from a condition that caught
    it. The automation keeps a trace for every trigger, whether or not its
    conditions passed.
    """
    fire_change = change(entity_id, state, **attributes)

    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        await fire_change(hass, freezer)
        await hass.async_block_till_done()
        traces = [
            trace
            for key, bucket in hass.data[DATA_TRACE].items()
            if key.startswith("automation.")
            for trace in bucket.all_traces()
        ]
        assert not traces, f"the trigger fired {len(traces)} times"

    return fire


def _oven_accepts_a_power_sensor(_state: Any, _questions: Any) -> None:
    """The oven input's selector must accept a sensor, or the UI cannot pick one."""
    path = BLUEPRINTS / "bedtime_check.yaml"
    config = load_yaml(path)["blueprint"]["input"]["what_to_watch"]["input"]["oven"]
    selector(config["selector"])(["sensor.oven_power"])


def _leaving_inputs(**overrides: Any) -> dict:
    base = {
        "zone": "zone.home",
        "lights": [],
        "windows": [],
        "appliances": [],
        "covers": [],
        "media": [],
        "hold": NO_WAIT,
        "cooldown": NO_WAIT,
        "threshold": 0.6,
        "handle_actions": run("yes", left="{{ left_on }}"),
        "clear_actions": run("no", left="{{ left_on }}"),
    }
    base.update(overrides)
    return base


_LEAVING_ANSWERS = {
    "needs_handling": NoulAnswer(noul=0.2),
    "urgency": ScoreAnswer(
        score=0.0,
        legend=_legend(URGENCY_LEVELS_HOME),
        probabilities={"0": 0.7, "1": 0.2, "2": 0.05, "3": 0.05},
        confidence=0.75,
    ),
}

# Q7: a television in standby is off as far as anyone leaving is concerned.
CASES["leaving_home_check:media_standby"] = Case(
    states={
        "zone.home": ("1", {}),
        "media_player.tv": ("standby", {"friendly_name": "Television"}),
    },
    inputs=_leaving_inputs(media=["media_player.tv"]),
    fire=change("zone.home", "0"),
    answers=_LEAVING_ANSWERS,
    expect={},
    asks=0,
)
CASES["leaving_home_check:media_playing"] = Case(
    states={
        "zone.home": ("1", {}),
        "media_player.tv": ("playing", {"friendly_name": "Television"}),
    },
    inputs=_leaving_inputs(media=["media_player.tv"]),
    fire=change("zone.home", "0"),
    answers=_LEAVING_ANSWERS,
    expect={"no": [{"left": "Television"}]},
)


def _bedtime_inputs(**overrides: Any) -> dict:
    base = {
        "doors": ["binary_sensor.front_door"],
        "windows": [],
        "oven": [],
        "lights": [],
        "locks": [],
        "garage_doors": [],
        "bedtime": "23:00:00",
        "trigger_entities": ["binary_sensor.phone_charging"],
        "trigger_hold": NO_WAIT,
        "cooldown": NO_WAIT,
        "threshold": 0.6,
        "handle_actions": run("yes", left="{{ left_on }}"),
        "clear_actions": run("no", left="{{ left_on }}"),
    }
    base.update(overrides)
    return base


_BEDTIME_ANSWERS = {
    "needs_handling": NoulAnswer(noul=0.9),
    "urgency": ScoreAnswer(
        score=3.0,
        legend=_legend(URGENCY_LEVELS_NIGHT),
        probabilities={"0": 0.02, "1": 0.03, "2": 0.15, "3": 0.8},
        confidence=0.85,
    ),
}
_BEDTIME_STATES = {
    "binary_sensor.front_door": ("on", {"friendly_name": "Front door"}),
    "binary_sensor.phone_charging": ("off", {}),
}

# C6: a phone plugged in at 03:35 is not bedtime. The default window is 21:00
# to 02:00.
CASES["bedtime_check:charger_outside_window"] = Case(
    states=_BEDTIME_STATES,
    inputs=_bedtime_inputs(),
    fire=_change_at(3, 35, "binary_sensor.phone_charging", "on"),
    answers=_BEDTIME_ANSWERS,
    expect={},
    asks=0,
)
CASES["bedtime_check:charger_inside_window"] = Case(
    states=_BEDTIME_STATES,
    inputs=_bedtime_inputs(),
    fire=_change_at(22, 45, "binary_sensor.phone_charging", "on"),
    answers=_BEDTIME_ANSWERS,
    expect={"yes": [{"left": "Front door"}]},
)
# R4: an oven with only a power sensor. 1500 W counts as on.
CASES["bedtime_check:oven_power_sensor"] = Case(
    states={
        "sensor.oven_power": (
            "1500",
            {"friendly_name": "Oven power", "unit_of_measurement": "W"},
        ),
    },
    inputs=_bedtime_inputs(doors=[], trigger_entities=[], oven=["sensor.oven_power"]),
    fire=at(23, 0),
    answers=_BEDTIME_ANSWERS,
    expect={"yes": [{"left": "Oven power"}]},
    check_request=_oven_accepts_a_power_sensor,
)
# An unavailable power sensor is not a reading, so it is not "on".
CASES["bedtime_check:oven_power_unavailable"] = Case(
    states={"sensor.oven_power": ("unavailable", {"friendly_name": "Oven power"})},
    inputs=_bedtime_inputs(doors=[], trigger_entities=[], oven=["sensor.oven_power"]),
    fire=at(23, 0),
    answers=_BEDTIME_ANSWERS,
    expect={},
    asks=0,
)

_CAMERA = "camera.front_door"
_CAMERA_BEFORE = {"description": "Nobody in view", "access_token": "a1"}

# B4: the camera's access token rotates every 5 minutes. That alone must not ask.
CASES["doorbell_caller:token_rotation"] = Case(
    states={_CAMERA: ("idle", _CAMERA_BEFORE)},
    inputs=_doorbell_inputs(
        transcript_entity=_CAMERA,
        transcript_attribute="description",
        other_actions=run("yes", who="{{ caller }}"),
    ),
    fire=_change_without_a_run(
        _CAMERA, "idle", description="Nobody in view", access_token="b2"
    ),
    answers=_doorbell_answer("other", 0.7),
    expect={},
    asks=0,
)
# The camera starts streaming, and its description stays the same.
CASES["doorbell_caller:state_change_same_text"] = Case(
    states={_CAMERA: ("idle", _CAMERA_BEFORE)},
    inputs=_doorbell_inputs(
        transcript_entity=_CAMERA,
        transcript_attribute="description",
        other_actions=run("yes", who="{{ caller }}"),
    ),
    fire=change(_CAMERA, "streaming", **_CAMERA_BEFORE),
    answers=_doorbell_answer("other", 0.7),
    expect={},
    asks=0,
)
# A new description asks, and Jev reads the description, not the state.
CASES["doorbell_caller:new_description"] = Case(
    states={_CAMERA: ("idle", _CAMERA_BEFORE)},
    inputs=_doorbell_inputs(
        transcript_entity=_CAMERA,
        transcript_attribute="description",
        delivery_actions=run("yes", who="{{ caller }}", t="{{ text }}"),
    ),
    fire=change(
        _CAMERA, "idle", description="A courier holding a box", access_token="a1"
    ),
    answers=_doorbell_answer("delivery", 0.84),
    expect={"yes": [{"who": "delivery", "t": "A courier holding a box"}]},
)
# With no attribute chosen, an attribute change on the entity must not ask.
CASES["doorbell_caller:attribute_only_on_state_text"] = Case(
    states={"input_text.doorbell_transcript": ("Hello", {"editable": True})},
    inputs=_doorbell_inputs(other_actions=run("yes", who="{{ caller }}")),
    fire=_change_without_a_run("input_text.doorbell_transcript", "Hello", editable=False),
    answers=_doorbell_answer("other", 0.7),
    expect={},
    asks=0,
)
for _bad in ("unavailable", "unknown", ""):
    CASES[f"doorbell_caller:skip_{_bad or 'empty'}"] = Case(
        states={"input_text.doorbell_transcript": ("Hello", {})},
        inputs=_doorbell_inputs(other_actions=run("yes", who="{{ caller }}")),
        fire=change("input_text.doorbell_transcript", _bad),
        answers=_doorbell_answer("other", 0.7),
        expect={},
        asks=0,
    )
# Q4: someone who lives here, and nobody at all.
CASES["doorbell_caller:resident"] = Case(
    states={"input_text.doorbell_transcript": ("", {})},
    inputs=_doorbell_inputs(resident_actions=run("yes", who="{{ caller }}")),
    fire=change("input_text.doorbell_transcript", "It's me, I forgot my keys"),
    answers=_doorbell_answer("resident", 0.8),
    expect={"yes": [{"who": "resident"}]},
)
CASES["doorbell_caller:nobody"] = Case(
    states={"input_text.doorbell_transcript": ("", {})},
    inputs=_doorbell_inputs(nobody_actions=run("yes", who="{{ caller }}")),
    fire=change("input_text.doorbell_transcript", "Silence, then a car driving past"),
    answers=_doorbell_answer("nobody", 0.8),
    expect={"yes": [{"who": "nobody"}]},
)


def _parcel_answer(choice: str) -> dict:
    options = (
        "arriving_today",
        "in_transit",
        "delivered",
        "needs_action",
        "not_about_parcel",
    )
    probabilities = {opt: 0.05 for opt in options}
    probabilities[choice] = 0.8
    return {
        "status": ChoiceAnswer(
            choice=choice, probabilities=probabilities, confidence=0.8
        ),
        "needs_someone": NoulAnswer(noul=0.1),
    }


# Q3: on its way, but not today.
CASES["parcel_today:in_transit"] = Case(
    states={"input_text.email_text": ("", {})},
    inputs=_parcel_inputs(in_transit_actions=run("yes", s="{{ status }}")),
    fire=change("input_text.email_text", "Your package ships and arrives on Friday"),
    answers=_parcel_answer("in_transit"),
    expect={"yes": [{"s": "in_transit"}]},
)
# C5: a newsletter holds none of the keywords, so it costs nothing.
CASES["parcel_today:newsletter_skipped"] = Case(
    states={"input_text.email_text": ("", {})},
    inputs=_parcel_inputs(arriving_actions=run("yes", s="{{ status }}")),
    fire=change("input_text.email_text", "Our autumn sale starts now, 20% off"),
    answers=_parcel_answer("not_about_parcel"),
    expect={},
    asks=0,
)
# A blank keyword list asks about every message.
CASES["parcel_today:no_keywords_asks"] = Case(
    states={"input_text.email_text": ("", {})},
    inputs=_parcel_inputs(keywords=[]),
    fire=change("input_text.email_text", "Our autumn sale starts now, 20% off"),
    answers=_parcel_answer("not_about_parcel"),
    expect={},
)
CASES["parcel_today:attribute_only_change"] = Case(
    states={"input_text.email_text": ("Your parcel was delivered", {"n": 1})},
    inputs=_parcel_inputs(delivered_actions=run("yes", s="{{ status }}")),
    fire=_change_without_a_run("input_text.email_text", "Your parcel was delivered", n=2),
    answers=_parcel_answer("delivered"),
    expect={},
    asks=0,
)
# With no keywords, so the empty-text rule is the only thing that skips it.
CASES["parcel_today:skip_unavailable"] = Case(
    states={"input_text.email_text": ("Your parcel was delivered", {})},
    inputs=_parcel_inputs(keywords=[], delivered_actions=run("yes", s="{{ status }}")),
    fire=change("input_text.email_text", "unavailable"),
    answers=_parcel_answer("delivered"),
    expect={},
    asks=0,
)
CASES["parcel_today:attribute_text"] = Case(
    states={"sensor.last_notification": ("posted", {"text": "Hi"})},
    inputs=_parcel_inputs(
        message_entity="sensor.last_notification",
        message_attribute="text",
        delivered_actions=run("yes", s="{{ status }}", t="{{ text }}"),
    ),
    fire=change("sensor.last_notification", "posted", text="Your parcel was delivered"),
    answers=_parcel_answer("delivered"),
    expect={"yes": [{"s": "delivered", "t": "Your parcel was delivered"}]},
)


def _motion_inputs(**overrides: Any) -> dict:
    base = {
        "motion_sensors": ["binary_sensor.hallway_motion"],
        "door_sensors": [],
        "persons": ["person.alex"],
        "hold": NO_WAIT,
        "cooldown": NO_WAIT,
        "background": "",
        "urgent_threshold": 2,
        "urgent_actions": run("yes", e="{{ explanation }}"),
        "explained_actions": run("no", e="{{ explanation }}"),
    }
    base.update(overrides)
    return base


_MOTION_STATES = {
    "binary_sensor.hallway_motion": ("off", {"friendly_name": "Hallway motion"}),
    "person.alex": ("not_home", {"friendly_name": "Alex"}),
}
_MOTION_PET = {
    "explanation": ChoiceAnswer(
        choice="pet",
        probabilities={
            "resident_arriving": 0.05,
            "pet": 0.85,
            "expected_visitor": 0.05,
            "unexplained": 0.05,
        },
        confidence=0.88,
    ),
    "urgency": ScoreAnswer(
        score=0.0,
        legend=_legend(URGENCY_LEVELS_AWAY),
        probabilities={"0": 0.8, "1": 0.1, "2": 0.05, "3": 0.05},
        confidence=0.7,
    ),
}


def _motion_sees_people(state: Any, _questions: Any) -> None:
    ids = [e["entity_id"] for e in state["entities"]]
    assert "person.alex" in ids, ids


# B6: a PIR that reports "on" for 30 seconds, with the default hold.
CASES["motion_while_away:short_pulse_default_hold"] = Case(
    states=_MOTION_STATES,
    inputs={k: v for k, v in _motion_inputs().items() if k != "hold"},
    fire=_pulses("binary_sensor.hallway_motion", count=1, on_for=30, every=60),
    answers=_MOTION_PET,
    expect={"no": [{"e": "pet"}]},
)
# A pet that trips the sensor every 6 minutes for 30 minutes: one call with the
# default 30 minute cooldown. The old 10 minute default asked 3 times.
CASES["motion_while_away:pet_default_cooldown"] = Case(
    states=_MOTION_STATES,
    inputs={k: v for k, v in _motion_inputs().items() if k not in ("hold", "cooldown")},
    fire=_pulses("binary_sensor.hallway_motion", count=5, on_for=30, every=360),
    answers=_MOTION_PET,
    expect={"no": [{"e": "pet"}]},
)
CASES["motion_while_away:people_in_target"] = Case(
    states=_MOTION_STATES,
    inputs=_motion_inputs(),
    fire=change("binary_sensor.hallway_motion", "on"),
    answers=_MOTION_PET,
    expect={"no": [{"e": "pet"}]},
    check_request=_motion_sees_people,
)
# A person with an unknown location is not known to be away.
CASES["motion_while_away:person_unknown"] = Case(
    states={**_MOTION_STATES, "person.alex": ("unknown", {})},
    inputs=_motion_inputs(),
    fire=change("binary_sensor.hallway_motion", "on"),
    answers=_MOTION_PET,
    expect={},
    asks=0,
)


def _calendar_inputs(**overrides: Any) -> dict:
    base = {
        "calendar_entity": "calendar.jev_test_calendar",
        "offset": {"minutes": 10},
        "background": "",
        "prep_actions": run("yes", s="{{ summary }}", w="{{ what }}"),
        "clear_actions": run("no", p="{{ probability }}", w="{{ what }}"),
    }
    base.update(overrides)
    return base


def _calendar_answers(noul: float, what: str) -> dict:
    options = ("bring", "leave_early", "buy_or_book", "nothing")
    probabilities = {opt: 0.1 for opt in options}
    probabilities[what] = 0.7
    return {
        "needs_prep": NoulAnswer(noul=noul),
        "what": ChoiceAnswer(choice=what, probabilities=probabilities, confidence=0.75),
    }


def _calendar_says_time_left(state: Any, questions: Any) -> None:
    assert state["starts_in"] == "10 minutes", state
    assert "starts in 10 minutes" in str(questions["needs_prep"].instructions)
    assert "tonight" not in str(questions["needs_prep"].instructions)


# B7: two events with the same start are both asked about.
CASES["calendar_prep:same_start"] = Case(
    inputs=_calendar_inputs(),
    fire=_calendar_fire("Dentist appointment", "School run", slow=True),
    answers=_calendar_answers(0.8, "bring"),
    expect={
        "yes": [
            {"s": "Dentist appointment", "w": "bring"},
            {"s": "School run", "w": "bring"},
        ]
    },
    asks=2,
)
# The question names the time left, not "tonight".
CASES["calendar_prep:time_left"] = Case(
    inputs=_calendar_inputs(),
    fire=_calendar_fire(),
    answers=_calendar_answers(0.8, "bring"),
    expect={"yes": [{"s": "Dentist appointment", "w": "bring"}]},
    check_request=_calendar_says_time_left,
)
# The threshold is an input. 0.8 is under a 0.9 threshold.
CASES["calendar_prep:threshold_input"] = Case(
    inputs=_calendar_inputs(threshold=0.9),
    fire=_calendar_fire(),
    answers=_calendar_answers(0.8, "bring"),
    expect={"no": [{"p": 0.8, "w": "bring"}]},
)
# needs_prep passes but the kind is nothing: the branches agree on nothing.
CASES["calendar_prep:nothing_wins"] = Case(
    inputs=_calendar_inputs(),
    fire=_calendar_fire(),
    answers=_calendar_answers(0.8, "nothing"),
    expect={"no": [{"p": 0.8, "w": "nothing"}]},
)
