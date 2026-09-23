"""jev.calibrate: a threshold measured against what was really true."""

from datetime import timedelta

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.jev.calibrate import Span, best, build_spans, outcome
from custom_components.jev.const import DOMAIN

PROBABILITY = "sensor.laundry_done"
TRUTH = "binary_sensor.laundry_door"


@pytest.fixture
def mock_recorder_before_hass(async_test_recorder):
    """The recorder's database has to exist before hass starts."""


@pytest.fixture(autouse=True)
def recorder(recorder_mock):
    return recorder_mock


def at_hours(*states):
    """States one hour apart, as the recorder would return them."""
    from homeassistant.core import State

    start = dt_util.parse_datetime("2026-09-22 08:00:00+00:00")
    return [
        State(entity_id, value, last_changed=start + timedelta(hours=hour))
        for entity_id, value, hour in states
    ], start


def test_every_change_of_either_entity_cuts_a_span():
    states, start = at_hours(
        ("sensor.p", "0.2", 0),
        ("binary_sensor.t", "off", 0),
        ("sensor.p", "0.7", 1),
        ("binary_sensor.t", "on", 1),
    )
    probabilities = [s for s in states if s.entity_id == "sensor.p"]
    truths = [s for s in states if s.entity_id == "binary_sensor.t"]
    spans = build_spans(probabilities, truths, "on", start, start + timedelta(hours=3))
    assert [(s.probability, s.truth, s.seconds) for s in spans] == [
        (0.2, False, 3600.0),
        (0.7, True, 7200.0),
    ]


def test_a_state_from_before_the_window_counts_from_its_start():
    states, start = at_hours(("sensor.p", "0.9", -5), ("binary_sensor.t", "on", -2))
    spans = build_spans(states[:1], states[1:], "on", start, start + timedelta(hours=1))
    assert spans == [Span(0.9, True, 3600.0)]


def test_time_without_a_number_is_left_out():
    """Unavailable while the API was down is not a probability of anything."""
    states, start = at_hours(
        ("sensor.p", "0.9", 0),
        ("binary_sensor.t", "on", 0),
        ("sensor.p", "unavailable", 1),
        ("sensor.p", "0.8", 2),
    )
    probabilities = [s for s in states if s.entity_id == "sensor.p"]
    truths = [s for s in states if s.entity_id == "binary_sensor.t"]
    spans = build_spans(probabilities, truths, "on", start, start + timedelta(hours=3))
    assert sum(s.seconds for s in spans) == 7200.0
    assert {s.probability for s in spans} == {0.9, 0.8}


def test_precision_and_recall_are_weighted_by_time():
    spans = [Span(0.8, True, 30), Span(0.8, False, 10), Span(0.2, True, 60)]
    result = outcome(spans, 0.5)
    assert result.precision == 0.75
    assert result.recall == 30 / 90


def test_nothing_said_yes_has_no_precision_and_no_f1():
    result = outcome([Span(0.2, True, 60), Span(0.1, False, 60)], 0.5)
    assert result.precision is None
    assert result.f1 == 0.0


def test_the_middle_of_the_tied_thresholds_is_returned():
    """Every threshold from 0.21 to 0.41 separates these perfectly. 0.31 is central."""
    spans = [
        Span(0.2, False, 3600),
        Span(0.7, True, 3600),
        Span(0.41, True, 3600),
        Span(0.1, False, 3600),
    ]
    result = best(spans)
    assert result.threshold == 0.31
    assert (result.precision, result.recall) == (1.0, 1.0)


async def record_a_day(hass, freezer):
    """Four hours: the door opens with the noul at 0.7, and closes at 0.1."""
    start = dt_util.utcnow() - timedelta(hours=4)
    for hour, probability, door in (
        (0, "0.2", "off"),
        (1, "0.7", "on"),
        (2, "0.41", "on"),
        (3, "0.1", "off"),
    ):
        freezer.move_to(start + timedelta(hours=hour))
        hass.states.async_set(PROBABILITY, probability)
        hass.states.async_set(TRUTH, door)
        await hass.async_block_till_done()
    freezer.move_to(start + timedelta(hours=4))
    await async_wait_recording_done(hass)


async def calibrate(hass, **data):
    return await hass.services.async_call(
        DOMAIN,
        "calibrate",
        {"entity_id": PROBABILITY, "truth_entity_id": TRUTH, **data},
        blocking=True,
        return_response=True,
    )


async def test_calibrate_reads_the_recorder_and_returns_the_best_threshold(
    hass, loaded_entry, mock_client, freezer
):
    mock_client.ask.reset_mock()
    await record_a_day(hass, freezer)
    result = await calibrate(hass)

    assert result["threshold"] == 0.31
    assert (result["precision"], result["recall"], result["f1"]) == (1.0, 1.0, 1.0)
    assert result["hours"] == 4.0
    assert result["hours_true"] == 2.0
    assert result["times_true"] == 1
    assert result["probability_changes"] == 4
    assert result["table"][0] == {
        "threshold": 0.1,
        "precision": 0.5,
        "recall": 1.0,
        "f1": 0.667,
    }
    # It reads history and nothing else, so it costs no tokens.
    assert mock_client.ask.await_count == 0


async def test_truth_that_never_happened_is_refused(hass, loaded_entry, freezer):
    await record_a_day(hass, freezer)
    with pytest.raises(ServiceValidationError) as err:
        await calibrate(hass, truth_state="open")
    assert err.value.translation_key == "calibrate_never_true"


async def test_truth_that_never_changed_is_refused(hass, loaded_entry, freezer):
    hass.states.async_set("binary_sensor.always", "on")
    await hass.async_block_till_done()
    freezer.tick(timedelta(hours=4))
    await record_a_day(hass, freezer)
    with pytest.raises(ServiceValidationError) as err:
        await calibrate(hass, truth_entity_id="binary_sensor.always")
    assert err.value.translation_key == "calibrate_always_true"


async def test_an_entity_with_no_history_is_refused(hass, loaded_entry):
    await async_wait_recording_done(hass)
    with pytest.raises(ServiceValidationError) as err:
        await calibrate(hass)
    assert err.value.translation_key == "calibrate_no_history"
    assert err.value.translation_placeholders["entity"] == PROBABILITY
