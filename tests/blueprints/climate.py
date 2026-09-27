"""Cases for the blueprints about heating, air, weather and the garden.

Three of these blueprints call `weather.get_forecasts`, and two of them operate one
device directly (`fan.set_percentage`, `switch.turn_on`/`turn_off`). No weather, fan
or switch platform is set up in this test Home Assistant, so the helpers below
register those services. The device services record what they were called with,
and a case says exactly which calls it expects, including none.

The clock in these tests is frozen at the real time the test starts, so a forecast
is built at the moment the blueprint asks for it, relative to that clock. A fixed
date would make the "hours ahead" and "after today" filters depend on when the
suite runs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util
from jevclient import ChoiceAnswer, NoulAnswer, ScoreAnswer
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .kit import NO_WAIT, Case, at, run

Fire = Callable[[HomeAssistant, Any], Awaitable[None]]
Row = dict[str, Any]


# -- firing ----------------------------------------------------------------------


def _change_now(entity_id: str, state: str, **attributes: Any) -> Fire:
    """Like `kit.change`, but does not tick the clock forward.

    Several of these blueprints carry a `time_pattern` recheck. The 61 minutes
    `kit.change` ticks would cross it and ask a second time.
    """

    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        hass.states.async_set(entity_id, state, attributes)
        await hass.async_block_till_done()

    return fire


def _tick(minutes: float) -> Fire:
    """Move the clock and fire whatever timers fall due, such as a recheck."""

    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        freezer.tick(timedelta(minutes=minutes))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

    return fire


def _steps(*fires: Fire) -> Fire:
    """Run several fires in order, as one case's story."""

    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        for step in fires:
            await step(hass, freezer)

    return fire


def with_prep(fire: Fire, *preps: Callable[[HomeAssistant], None]) -> Fire:
    """Run the prep steps, then the case's real trigger."""

    async def _fire(hass: HomeAssistant, freezer: Any) -> None:
        for prep in preps:
            prep(hass)
        await fire(hass, freezer)

    return _fire


def _expect_device_calls(
    fire: Fire, expected: dict[tuple[str, str], list[dict[str, Any]]]
) -> Fire:
    """Register the device services, run the fire, and check what they received.

    Every service in `expected` is registered, so a case that expects `[]` for
    `switch.turn_off` fails when the blueprint calls it.
    """

    async def _fire(hass: HomeAssistant, freezer: Any) -> None:
        received: dict[tuple[str, str], list[dict[str, Any]]] = {k: [] for k in expected}

        def recorder(key: tuple[str, str]) -> Callable[[ServiceCall], Awaitable[None]]:
            async def _handle(call: ServiceCall) -> None:
                received[key].append(dict(call.data))

            return _handle

        for key in expected:
            hass.services.async_register(*key, recorder(key))
        await fire(hass, freezer)
        await hass.async_block_till_done()
        assert received == expected, received

    return _fire


def _set_in_cooldown(entity_id: str, state: str) -> Fire:
    """Set a state while a run may be waiting out a real cooldown.

    `async_block_till_done` waits for a running `delay:`, so it cannot be used
    here. The loop's clock is frozen too, so a real sleep never ends. Yielding to
    the loop lets the trigger and the mocked call finish, since neither does I/O.
    """

    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        hass.states.async_set(entity_id, state)
        for _ in range(100):
            await asyncio.sleep(0)

    return fire


def _automations_are_on(fire: Fire) -> Fire:
    """A trigger Home Assistant refuses leaves the automation unavailable."""

    async def _fire(hass: HomeAssistant, freezer: Any) -> None:
        states = {s.entity_id: s.state for s in hass.states.async_all("automation")}
        assert set(states.values()) == {"on"}, states
        await fire(hass, freezer)

    return _fire


# -- forecasts ---------------------------------------------------------------------


def _hour_start(offset: int) -> datetime:
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    return now + timedelta(hours=offset)


def _day_start(offset: int) -> datetime:
    today = dt_util.start_of_local_day()
    return dt_util.as_utc(today + timedelta(days=offset))


def _stub_forecast(
    rows: list[Row], *, daily: bool = False, first: int = 0
) -> Callable[[HomeAssistant], None]:
    """A prep step faking `weather.get_forecasts` the way a real entity answers it.

    The response shape matches the real service: `{entity_id: {"forecast": [...]}}`.
    Row `i` is dated `first + i` hours (or days) from now, as a UTC ISO string,
    which is what the providers send.
    """

    async def _handle(call: ServiceCall) -> dict[str, Any]:
        entity_ids = call.data.get("entity_id") or []
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        start = _day_start if daily else _hour_start
        forecast = [
            {"datetime": start(first + i).isoformat(), **row}
            for i, row in enumerate(rows)
        ]
        return {eid: {"forecast": forecast} for eid in entity_ids}

    def prep(hass: HomeAssistant) -> None:
        if not hass.services.has_service("weather", "get_forecasts"):
            hass.services.async_register(
                "weather",
                "get_forecasts",
                _handle,
                supports_response=SupportsResponse.ONLY,
            )

    return prep


def _local_hour(offset: int) -> str:
    return dt_util.as_local(_hour_start(offset)).strftime("%H:%M")


def _weekday(offset: int) -> str:
    return dt_util.as_local(_day_start(offset)).strftime("%A")


RAIN = [
    {"condition": "rainy", "precipitation_probability": 80},
    {"condition": "rainy", "precipitation_probability": 85},
    {"condition": "rainy", "precipitation_probability": 90},
]
SHOWERS = [
    {"condition": "cloudy", "precipitation_probability": 40},
    {"condition": "cloudy", "precipitation_probability": 35},
]
DRY = [
    {"condition": "sunny", "precipitation_probability": 0, "temperature": 22},
    {"condition": "sunny", "precipitation_probability": 10, "temperature": 24},
    {"condition": "sunny", "precipitation_probability": 5, "temperature": 24},
]
# A provider that leaves the rain chance out.
RAIN_NO_CHANCE = [{"condition": "rainy"}, {"condition": "pouring"}]
CLOUDS_NO_CHANCE = [{"condition": "cloudy"}, {"condition": "partlycloudy"}]
# Met.no gives the rain amount in mm and no chance.
MM_ONLY_WET = [
    {"condition": "cloudy", "precipitation": 0.0},
    {"condition": "cloudy", "precipitation": 0.6},
]
MM_ONLY_DRY = [
    {"condition": "partlycloudy", "precipitation": 0.0},
    {"condition": "cloudy", "precipitation": 0.0},
]
# Two hours already gone, then rain. Only the current hour and later count.
PAST_THEN_RAIN = [
    {"condition": "fog", "precipitation_probability": 95},
    {"condition": "fog", "precipitation_probability": 95},
    *RAIN,
]
# A daily forecast that starts today. Today is a storm, and it must not be read
# as "tomorrow".
DAILY_FROM_TODAY = [
    {"condition": "lightning", "precipitation_probability": 90, "temperature": 18},
    {"condition": "sunny", "precipitation_probability": 0, "temperature": 31},
    {"condition": "sunny", "precipitation_probability": 5, "temperature": 30},
]
DAILY_HOT_DRY = DAILY_FROM_TODAY[1:]

CLOTHING_LEGEND = {
    "0": "A t-shirt is enough",
    "1": "A light jacket is enough",
    "2": "A coat is needed",
    "3": "A winter coat is needed",
}


def _note(state: Any) -> str:
    return " ".join(state["note"].split())


# -- window_open_while_heating ---------------------------------------------------

WINDOW = "binary_sensor.window_kitchen"
KITCHEN = {"friendly_name": "Kitchen window"}
OUTDOOR = ("5", {"unit_of_measurement": "°C"})


def _window_inputs(**overrides: Any) -> dict[str, Any]:
    return {
        "windows": [WINDOW],
        "heating_entity": "climate.living_room",
        "outdoor_temp": "sensor.outdoor_temp",
        "hold": NO_WAIT,
        "cooldown": NO_WAIT,
        "background": "The kitchen window opens right onto the street.",
        "threshold": 0.6,
        "wasting_heat_actions": run("yes", p="{{ probability }}", w="{{ open_windows }}"),
        "ok_actions": run("other", p="{{ probability }}"),
        **overrides,
    }


def _window_case(
    climate: tuple[str, dict[str, Any]],
    fire: Fire,
    *,
    window_open: bool = False,
    noul: float = 0.75,
    asks: int = 1,
    check: Callable[..., None] | None = None,
    **overrides: Any,
) -> Case:
    states = {"climate.living_room": climate, "sensor.outdoor_temp": OUTDOOR}
    if window_open:
        states[WINDOW] = ("on", KITCHEN)
    branch = "yes" if noul >= 0.6 else "other"
    data = {"p": noul, "w": "Kitchen window"} if branch == "yes" else {"p": noul}
    return Case(
        states=states,
        inputs=_window_inputs(**overrides),
        fire=fire,
        answers={"answer": NoulAnswer(noul=noul)},
        expect={branch: [data] * asks} if asks else {},
        asks=asks,
        check_request=check,
    )


def _window_request(heating: str) -> Callable[..., None]:
    def check(state: Any, questions: dict[str, Any]) -> None:
        assert _note(state) == (
            f"Open for at least 0 minutes: Kitchen window. The heating is {heating}."
        )
        ids = [e["entity_id"] for e in state["entities"]]
        assert ids == [WINDOW, "climate.living_room", "sensor.outdoor_temp"]
        assert questions["answer"].true == "Left open long enough to waste heat"

    return check


OPEN_KITCHEN = _change_now(WINDOW, "on", **KITCHEN)
HEATING = ("heat", {"hvac_action": "heating"})
IDLE_IN_HEAT = ("heat", {"hvac_action": "idle"})

WINDOW_CASES: dict[str, Case] = {
    "window_open_while_heating:wasting_heat": _window_case(
        HEATING, OPEN_KITCHEN, check=_window_request("heating")
    ),
    "window_open_while_heating:ok": _window_case(HEATING, OPEN_KITCHEN, noul=0.2),
    "window_open_while_heating:heating_off": _window_case(
        ("off", {}), OPEN_KITCHEN, noul=0.9, asks=0
    ),
    # B3: a valve with window detection goes idle on exactly this event.
    "window_open_while_heating:valve_idles_for_the_window": _window_case(
        IDLE_IN_HEAT,
        OPEN_KITCHEN,
        check=_window_request(
            "set to heat but idle, possibly because a valve detected the open window"
        ),
    ),
    # B3: cooling is not heating.
    "window_open_while_heating:cooling_is_not_heating": _window_case(
        ("cool", {"hvac_action": "cooling"}), OPEN_KITCHEN, asks=0
    ),
    # B3: a window that stays open is asked about again at each recheck.
    "window_open_while_heating:still_open_later": _window_case(
        HEATING, _steps(_tick(15), _tick(15)), window_open=True, asks=2
    ),
    # A recheck does not ask about a window that has not been open for the hold.
    "window_open_while_heating:recheck_waits_for_the_hold": _window_case(
        HEATING, _tick(15), window_open=True, asks=0, hold={"minutes": 30}
    ),
    # B3: the heating starts while a window is already open.
    "window_open_while_heating:heating_starts": _window_case(
        IDLE_IN_HEAT,
        _change_now("climate.living_room", "heat", hvac_action="heating"),
        window_open=True,
    ),
    "window_open_while_heating:heating_turned_on": _window_case(
        ("off", {"hvac_action": "off"}),
        _change_now("climate.living_room", "heat", hvac_action="idle"),
        window_open=True,
    ),
}


# -- ventilation_speed -----------------------------------------------------------

FAN = "fan.bathroom_fan"


def _ventilation_case(
    co2: str,
    fire: Fire,
    *,
    answer: ChoiceAnswer,
    fan_calls: list[dict[str, Any]],
    expect: dict[str, list[dict[str, Any]]],
    asks: int = 1,
    humidity: str = "55",
    check: Callable[..., None] | None = None,
    **overrides: Any,
) -> Case:
    return Case(
        states={
            "sensor.co2": (co2, {"unit_of_measurement": "ppm"}),
            "sensor.humidity": (humidity, {"unit_of_measurement": "%"}),
            FAN: ("on", {"percentage": 30}),
        },
        inputs={
            "co2_sensor": "sensor.co2",
            "humidity_sensor": "sensor.humidity",
            "occupancy_sensor": "",
            "co2_high": 1000,
            "co2_low": 800,
            "humidity_high": 60,
            "humidity_low": 50,
            "hold": NO_WAIT,
            "cooldown": NO_WAIT,
            "fan_entity": FAN,
            "low_percentage": 30,
            "medium_percentage": 60,
            "high_percentage": 100,
            "min_confidence": 0.6,
            "background": "The shower is in this room.",
            "extra_actions": run("yes", s="{{ speed }}", c="{{ confidence }}"),
            **overrides,
        },
        fire=_expect_device_calls(fire, {("fan", "set_percentage"): fan_calls}),
        answers={"answer": answer},
        expect=expect,
        asks=asks,
        check_request=check,
    )


def _choice(choice: str, confidence: float) -> ChoiceAnswer:
    rest = (1 - confidence) / 3
    speeds = ["off", "low", "medium", "high"]
    probabilities = {s: confidence if s == choice else rest for s in speeds}
    return ChoiceAnswer(choice=choice, probabilities=probabilities, confidence=confidence)


def _ventilation_request(state: Any, questions: dict[str, Any]) -> None:
    assert _note(state) == (
        "CO2 is stale, above 1200 ppm. Humidity is between 50% and 60%."
    )
    background = " ".join(questions["answer"].instructions["background"].split())
    assert background.startswith("CO2 over 1200 ppm is stale air, under 700 ppm")
    assert background.endswith("The shower is in this room.")


def _co2(value: str, **attributes: Any) -> Fire:
    return _change_now("sensor.co2", value, unit_of_measurement="ppm", **attributes)


def _humidity(value: str) -> Fire:
    return _change_now("sensor.humidity", value, unit_of_measurement="%")


SET_HIGH = [{"entity_id": [FAN], "percentage": 100}]

VENTILATION_CASES: dict[str, Case] = {
    # R3: the thresholds are inputs, and the ones set here reach Jev.
    "ventilation_speed:fan_changes": _ventilation_case(
        "650",
        _co2("1400"),
        answer=_choice("high", 0.9),
        fan_calls=SET_HIGH,
        expect={"yes": [{"s": "high", "c": 0.9}]},
        check=_ventilation_request,
        co2_high=1200,
        co2_low=700,
    ),
    "ventilation_speed:below_confidence": _ventilation_case(
        "700",
        _co2("1200"),
        answer=_choice("medium", 0.3),
        fan_calls=[],
        expect={"yes": [{"s": "medium", "c": 0.3}]},
    ),
    "ventilation_speed:no_change": _ventilation_case(
        "900",
        _co2("700"),
        answer=_choice("low", 0.8),
        fan_calls=[],
        expect={"yes": [{"s": "low", "c": 0.8}]},
    ),
    # C2: readings that move inside a band, and an attribute-only update, cost
    # nothing.
    # R3: 1100 ppm is stale by the default, fine under a 1200 ppm threshold.
    "ventilation_speed:custom_threshold": _ventilation_case(
        "900",
        _co2("1100"),
        answer=_choice("high", 0.9),
        fan_calls=[],
        expect={},
        asks=0,
        co2_high=1200,
    ),
    "ventilation_speed:moves_within_a_band": _ventilation_case(
        "850",
        _steps(_co2("900"), _co2("950"), _co2("980"), _co2("980", source="poll")),
        answer=_choice("low", 0.8),
        fan_calls=[],
        expect={},
        asks=0,
    ),
    # C2: humidity climbing 1% at a time asks once, when it crosses 60%.
    "ventilation_speed:humidity_ramp_asks_once": _ventilation_case(
        "700",
        _steps(*(_humidity(str(h)) for h in range(56, 66))),
        answer=_choice("high", 0.9),
        fan_calls=SET_HIGH,
        expect={"yes": [{"s": "high", "c": 0.9}]},
    ),
}


# -- rain_and_open_windows -------------------------------------------------------

WEATHER = "weather.home_weather"


def _rain_case(
    rows: list[Row],
    fire: Fire,
    *,
    patio: str = "on",
    noul: float = 0.85,
    asks: int = 1,
    first: int = 0,
    check: Callable[..., None] | None = None,
    **overrides: Any,
) -> Case:
    expect: dict[str, list[dict[str, Any]]] = {}
    if asks:
        branch = "yes" if noul >= 0.6 else "other"
        expect = {branch: [{"p": noul, "o": "Patio door"}] * asks}
    return Case(
        states={
            "binary_sensor.patio_door": (patio, {"friendly_name": "Patio door"}),
            "cover.awning": ("closed", {"friendly_name": "Awning"}),
            WEATHER: ("cloudy", {"temperature": 14}),
        },
        inputs={
            "weather_entity": WEATHER,
            "open_things": ["binary_sensor.patio_door", "cover.awning"],
            "hours_ahead": 3,
            "interval": "/30",
            "rain_floor": 30,
            "weather_hold": NO_WAIT,
            "cooldown": NO_WAIT,
            "background": "",
            "threshold": 0.6,
            "rain_coming_actions": run(
                "yes", p="{{ probability }}", o="{{ open_things }}"
            ),
            "clear_actions": run("other", p="{{ probability }}", o="{{ open_things }}"),
            **overrides,
        },
        fire=with_prep(fire, _stub_forecast(rows, first=first)),
        answers={"answer": NoulAnswer(noul=noul)},
        expect=expect,
        asks=asks,
        check_request=check,
    )


def _rain_request(state: Any, _questions: dict[str, Any]) -> None:
    assert _note(state) == (
        "Open right now: Patio door. Forecast for the next 3 hours: "
        f"At {_local_hour(0)}: rainy, 80% chance of rain. "
        f"At {_local_hour(1)}: rainy, 85% chance of rain. "
        f"At {_local_hour(2)}: rainy, 90% chance of rain."
    )


def _rain_amount(state: Any, _questions: dict[str, Any]) -> None:
    assert f"At {_local_hour(1)}: cloudy, 0.6 mm of rain." in _note(state)


def _rain_unknown_chance(state: Any, _questions: dict[str, Any]) -> None:
    note = _note(state)
    assert "0%" not in note
    assert f"At {_local_hour(0)}: rainy, chance of rain unknown." in note


TURNS_RAINY = _change_now(WEATHER, "rainy", temperature=14)

RAIN_CASES: dict[str, Case] = {
    "rain_and_open_windows:rain_coming": _rain_case(
        RAIN, TURNS_RAINY, check=_rain_request
    ),
    # Rain chance above the floor, and Jev judges it will stay dry enough.
    "rain_and_open_windows:clear": _rain_case(SHOWERS, TURNS_RAINY, noul=0.1),
    "rain_and_open_windows:nothing_open": _rain_case(
        RAIN, TURNS_RAINY, patio="off", asks=0
    ),
    # C3: a dry forecast is answered by the rain floor, with no call.
    "rain_and_open_windows:dry_forecast": _rain_case(
        DRY, _steps(_tick(30), _tick(30)), asks=0
    ),
    # C3: a weather entity's attributes update all the time, and that is no reason
    # to ask.
    "rain_and_open_windows:attribute_update": _rain_case(
        RAIN, _change_now(WEATHER, "cloudy", temperature=15), asks=0
    ),
    # B9: a provider with no rain chance is "unknown", never "0%".
    "rain_and_open_windows:chance_missing": _rain_case(
        RAIN_NO_CHANCE, TURNS_RAINY, check=_rain_unknown_chance
    ),
    "rain_and_open_windows:chance_missing_and_cloudy": _rain_case(
        CLOUDS_NO_CHANCE, TURNS_RAINY, asks=0
    ),
    # A provider with only the amount: more than 0 mm is a reason to ask.
    "rain_and_open_windows:amount_only": _rain_case(
        MM_ONLY_WET, TURNS_RAINY, check=_rain_amount
    ),
    "rain_and_open_windows:amount_only_dry": _rain_case(MM_ONLY_DRY, TURNS_RAINY, asks=0),
    # B9: hours that have already gone are not the next 3 hours.
    "rain_and_open_windows:past_hours_dropped": _rain_case(
        PAST_THEN_RAIN, TURNS_RAINY, first=-2, check=_rain_request
    ),
    # B1: "Every hour" is minute 0, which Home Assistant accepts.
    "rain_and_open_windows:every_hour": _rain_case(
        RAIN, _automations_are_on(_tick(60)), interval="0"
    ),
}


# -- water_the_garden ------------------------------------------------------------


def _garden_case(
    soil: tuple[str, str],
    rows: list[Row],
    *,
    first: int,
    noul: float,
    rain_sensor: str = "",
    check: Callable[..., None] | None = None,
) -> Case:
    branch = "yes" if noul >= 0.6 else "other"
    return Case(
        states={
            "sensor.soil_veggie_patch": (soil[0], {"friendly_name": "Veggie patch"}),
            "sensor.soil_lawn": (soil[1], {"friendly_name": "Lawn"}),
            WEATHER: ("sunny", {}),
        },
        inputs={
            "soil_sensors": ["sensor.soil_veggie_patch", "sensor.soil_lawn"],
            "weather_entity": WEATHER,
            "rain_sensor": rain_sensor,
            "evening_time": "20:00:00",
            "background": "",
            "threshold": 0.6,
            "water_actions": run("yes", p="{{ probability }}"),
            "skip_actions": run("other", p="{{ probability }}"),
        },
        fire=with_prep(at(20, 0), _stub_forecast(rows, daily=True, first=first)),
        answers={"answer": NoulAnswer(noul=noul)},
        expect={branch: [{"p": noul}]},
        check_request=check,
    )


def _garden_request(state: Any, _questions: dict[str, Any]) -> None:
    note = _note(state)
    assert note == (
        "Soil moisture: Veggie patch: 18%, Lawn: unknown. Forecast for the next "
        f"two days: {_weekday(1)}: sunny, 0% chance of rain, high 31 degrees. "
        f"{_weekday(2)}: sunny, 5% chance of rain, high 30 degrees."
    ), note


GARDEN_CASES: dict[str, Case] = {
    "water_the_garden:needs_water": _garden_case(
        ("18", "unavailable"), DAILY_HOT_DRY, first=1, noul=0.8, check=_garden_request
    ),
    "water_the_garden:skip": _garden_case(
        ("55", "60"),
        DAILY_HOT_DRY,
        first=1,
        noul=0.15,
        rain_sensor="binary_sensor.rain_gauge",
    ),
    # B9/T6: some providers start the daily forecast at today. At 20:00 today is
    # nearly over, and the storm in it is not tomorrow's weather.
    "water_the_garden:forecast_starts_today": _garden_case(
        ("18", "unavailable"),
        DAILY_FROM_TODAY,
        first=0,
        noul=0.8,
        check=_garden_request,
    ),
}


# -- umbrella_and_coat -----------------------------------------------------------

# The morning time trigger must not fall inside the minutes a case ticks through.
# Twelve hours from when the module loads is far from any test's clock.
FAR_MORNING = (dt_util.now() + timedelta(hours=12)).strftime("%H:%M:00")


def _umbrella_inputs(**overrides: Any) -> dict[str, Any]:
    return {
        "weather_entity": WEATHER,
        "morning_time": "07:00:00",
        "person": [],
        "hold": NO_WAIT,
        "cooldown": NO_WAIT,
        "umbrella_threshold": 0.5,
        "background": "",
        "tell_actions": run(
            "yes", up="{{ umbrella_probability }}", u="{{ umbrella }}", c="{{ clothing }}"
        ),
        **overrides,
    }


def _clothing(level: int) -> ScoreAnswer:
    probabilities = {str(i): 0.8 if i == level else 0.2 / 3 for i in range(4)}
    return ScoreAnswer(
        score=float(level),
        legend=CLOTHING_LEGEND,
        probabilities=probabilities,
        confidence=0.8,
    )


def _umbrella_request(state: Any, questions: dict[str, Any]) -> None:
    note = _note(state)
    assert note.startswith(
        f"Hourly forecast: At {_local_hour(0)}: rainy, chance of rain unknown, "
        "14 degrees. "
    )
    assert f"At {_local_hour(2)}: rainy, 90% chance of rain, temperature unknown." in note
    assert f"At {_local_hour(3)}: cloudy, 0.4 mm of rain, 13 degrees." in note
    assert "fog" not in note
    assert set(questions) == {"umbrella", "clothing"}


UMBRELLA_MORNING_ROWS = [
    {"condition": "fog", "precipitation_probability": 0, "temperature": 9},
    {"condition": "fog", "precipitation_probability": 0, "temperature": 9},
    {"condition": "rainy", "temperature": 14},
    {"condition": "rainy", "precipitation_probability": 85, "temperature": 15},
    {"condition": "rainy", "precipitation_probability": 90},
    {"condition": "cloudy", "precipitation": 0.4, "temperature": 13},
]
ALICE = "person.alice"


def _flaps(times: int, seconds_away: int) -> Fire:
    """A GPS location that jumps away and back, each time for a few seconds."""
    steps: list[Fire] = []
    for _ in range(times):
        steps += [
            _change_now(ALICE, "not_home"),
            _tick(seconds_away / 60),
            _change_now(ALICE, "home"),
            _tick(10 / 60),
        ]
    return _steps(*steps)


def _leaves(minutes_away: float) -> Fire:
    return _steps(_change_now(ALICE, "not_home"), _tick(minutes_away))


UMBRELLA_CASES: dict[str, Case] = {
    "umbrella_and_coat:morning": Case(
        states={WEATHER: ("rainy", {})},
        inputs=_umbrella_inputs(),
        fire=with_prep(at(7, 0), _stub_forecast(UMBRELLA_MORNING_ROWS, first=-2)),
        answers={"umbrella": NoulAnswer(noul=0.7), "clothing": _clothing(2)},
        expect={"yes": [{"up": 0.7, "u": True, "c": "A coat is needed"}]},
        check_request=_umbrella_request,
    ),
    "umbrella_and_coat:person_leaves": Case(
        states={WEATHER: ("sunny", {}), ALICE: ("home", {})},
        inputs=_umbrella_inputs(morning_time=FAR_MORNING, person=[ALICE]),
        fire=with_prep(_change_now(ALICE, "not_home"), _stub_forecast(DRY)),
        answers={"umbrella": NoulAnswer(noul=0.3), "clothing": _clothing(0)},
        expect={"yes": [{"up": 0.3, "u": False, "c": "A t-shirt is enough"}]},
    ),
    # C8: five GPS flaps in three minutes ask nothing. Staying away past the hold
    # asks once.
    "umbrella_and_coat:gps_flaps": Case(
        states={WEATHER: ("sunny", {}), ALICE: ("home", {})},
        inputs=_umbrella_inputs(
            morning_time=FAR_MORNING, person=[ALICE], hold={"minutes": 2}
        ),
        fire=with_prep(_steps(_flaps(5, 25), _leaves(3)), _stub_forecast(DRY)),
        answers={"umbrella": NoulAnswer(noul=0.3), "clothing": _clothing(0)},
        expect={"yes": [{"up": 0.3, "u": False, "c": "A t-shirt is enough"}]},
    ),
    # C8: a second departure inside the cooldown does not ask again.
    "umbrella_and_coat:cooldown": Case(
        states={WEATHER: ("sunny", {}), ALICE: ("home", {})},
        inputs=_umbrella_inputs(
            morning_time=FAR_MORNING, person=[ALICE], cooldown={"minutes": 30}
        ),
        fire=with_prep(
            _steps(
                _set_in_cooldown(ALICE, "not_home"),
                _set_in_cooldown(ALICE, "home"),
                _set_in_cooldown(ALICE, "not_home"),
                _tick(31),
            ),
            _stub_forecast(DRY),
        ),
        answers={"umbrella": NoulAnswer(noul=0.3), "clothing": _clothing(0)},
        expect={"yes": [{"up": 0.3, "u": False, "c": "A t-shirt is enough"}]},
    ),
}


# -- ev_charge_now ---------------------------------------------------------------

CHARGER = "switch.ev_charger"
PLUGGED = "binary_sensor.ev_plugged_in"


def _ev_case(
    *,
    battery: str,
    price: str,
    charger: str,
    noul: float,
    turn_on: list[dict[str, Any]],
    turn_off: list[dict[str, Any]],
    asks: int = 1,
    fire: Fire | None = None,
    plugged: str | None = None,
    check: Callable[..., None] | None = None,
    **overrides: Any,
) -> Case:
    states = {
        "sensor.ev_battery": (battery, {"unit_of_measurement": "%"}),
        "sensor.elec_price_now": (price, {}),
        "sensor.elec_price_avg": ("0.20", {}),
        CHARGER: (charger, {}),
    }
    if plugged is not None:
        states[PLUGGED] = (plugged, {})
    return Case(
        states=states,
        inputs={
            "plugged_in_sensor": PLUGGED,
            "battery_sensor": "sensor.ev_battery",
            "price_sensor": "sensor.elec_price_now",
            "average_price_sensor": "sensor.elec_price_avg",
            "solar_sensor": "",
            "departure_time": "07:00:00",
            "plugged_in_hold": NO_WAIT,
            "recheck_interval": "0",
            "target_battery": 80,
            "cooldown": NO_WAIT,
            "charger_switch": CHARGER,
            "charge_on_threshold": 0.65,
            "charge_off_threshold": 0.35,
            "background": "",
            "extra_actions": run("yes", p="{{ probability }}"),
            **overrides,
        },
        fire=_expect_device_calls(
            fire or _change_now(PLUGGED, "on"),
            {("switch", "turn_on"): turn_on, ("switch", "turn_off"): turn_off},
        ),
        answers={"answer": NoulAnswer(noul=noul)},
        expect={"yes": [{"p": noul}] * asks} if asks else {},
        asks=asks,
        check_request=check,
    )


def _ev_request(state: Any, _questions: dict[str, Any]) -> None:
    note = _note(state)
    assert note.startswith(
        "Battery at 40.0 percent, and the target is 80 percent. Price right now is "
        "well below average (0.1 versus an average of 0.2). There is no solar "
        "reading. Departure in about "
    )
    assert note.endswith("hours. Charger is currently off.")


SWITCHED = [{"entity_id": [CHARGER]}]

EV_CASES: dict[str, Case] = {
    "ev_charge_now:turn_on": _ev_case(
        battery="40",
        price="0.10",
        charger="off",
        noul=0.8,
        turn_on=SWITCHED,
        turn_off=[],
        check=_ev_request,
    ),
    "ev_charge_now:turn_off": _ev_case(
        battery="60", price="0.30", charger="on", noul=0.2, turn_on=[], turn_off=SWITCHED
    ),
    # Between the two thresholds the charger stays as it is.
    "ev_charge_now:hysteresis_hold": _ev_case(
        battery="60", price="0.20", charger="off", noul=0.5, turn_on=[], turn_off=[]
    ),
    # T1: the switch already matches, so nothing is called.
    "ev_charge_now:already_charging": _ev_case(
        battery="60", price="0.10", charger="on", noul=0.8, turn_on=[], turn_off=[]
    ),
    "ev_charge_now:already_off": _ev_case(
        battery="60", price="0.30", charger="off", noul=0.2, turn_on=[], turn_off=[]
    ),
    # B5: an unavailable battery is not 0 percent, and an unavailable price is not
    # a cheap one.
    "ev_charge_now:battery_unavailable": _ev_case(
        battery="unavailable",
        price="0.10",
        charger="off",
        noul=0.9,
        turn_on=[],
        turn_off=[],
        asks=0,
    ),
    "ev_charge_now:price_unavailable": _ev_case(
        battery="40",
        price="unavailable",
        charger="off",
        noul=0.9,
        turn_on=[],
        turn_off=[],
        asks=0,
    ),
    # B5: at the target battery level it stops asking.
    "ev_charge_now:at_target": _ev_case(
        battery="80",
        price="0.10",
        charger="off",
        noul=0.9,
        turn_on=[],
        turn_off=[],
        asks=0,
    ),
    # B1/C4: the hourly recheck is minute 0, which Home Assistant accepts.
    "ev_charge_now:hourly_recheck": _ev_case(
        battery="40",
        price="0.10",
        charger="off",
        noul=0.8,
        turn_on=SWITCHED,
        turn_off=[],
        plugged="on",
        fire=_automations_are_on(_tick(60)),
    ),
}


CASES: dict[str, Case] = {
    **WINDOW_CASES,
    **VENTILATION_CASES,
    **RAIN_CASES,
    **GARDEN_CASES,
    **UMBRELLA_CASES,
    **EV_CASES,
}
