"""Cases for the blueprints that ask any question the user writes."""

import asyncio
from collections.abc import Callable
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from jevclient import ChoiceAnswer, NoulAnswer, ScoreAnswer
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

from .kit import NO_WAIT, Case, at, change, run


def _laundry_question(state: Any, questions: dict[str, Any]) -> None:
    question = questions["answer"]
    assert question.true == "Done and still in the machine"
    assert question.false is None
    assert [e["entity_id"] for e in state["entities"]] == ["sensor.washer_power"]


CASES: dict[str, Case] = {
    "yes_no_question": Case(
        states={"sensor.washer_power": ("1.2", {"unit_of_measurement": "W"})},
        inputs={
            "entities": ["sensor.washer_power"],
            "hold": NO_WAIT,
            "cooldown": NO_WAIT,
            "question": "Is the laundry finished but still in the machine?",
            "yes_means": "Done and still in the machine",
            "yes_actions": run(
                "yes", p="{{ probability }}", yes="{{ answered_yes }}", q="{{ question }}"
            ),
            "no_actions": run("no", p="{{ probability }}"),
        },
        fire=change("sensor.washer_power", "0.8", unit_of_measurement="W"),
        answers={"answer": NoulAnswer(noul=0.83)},
        expect={
            "yes": [
                {
                    "p": 0.83,
                    "yes": True,
                    "q": "Is the laundry finished but still in the machine?",
                }
            ]
        },
        check_request=_laundry_question,
    ),
}


# ---------------------------------------------------------------------------
# pick_one_of_several
# ---------------------------------------------------------------------------

_PICK_ONE_INPUTS = {
    "entities": ["sensor.intercom_transcript"],
    "hold": NO_WAIT,
    "cooldown": NO_WAIT,
    "question": "What kind of caller is at the door?",
    "option_1_name": "delivery",
    "option_1_description": "A parcel or food delivery",
    "option_2_name": "neighbour",
    "option_2_description": "Somebody who lives nearby",
    "option_3_name": "sales",
    "option_3_description": "Selling something at the door",
    "option_4_name": "",
    "option_4_description": "",
    "minimum_confidence": 0.5,
    "option_1_actions": run("yes", choice="{{ choice }}"),
    "option_2_actions": run(
        "other",
        choice="{{ choice }}",
        confidence="{{ confidence }}",
        probabilities="{{ probabilities }}",
    ),
    "option_3_actions": run("no", choice="{{ choice }}"),
    "option_4_actions": [],
    "unsure_actions": run("no", unsure="yes"),
}

CASES["pick_one_of_several:match"] = Case(
    states={"sensor.intercom_transcript": ("idle", {})},
    inputs=_PICK_ONE_INPUTS,
    fire=change("sensor.intercom_transcript", "Package for number 31"),
    answers={
        "answer": ChoiceAnswer(
            choice="neighbour",
            probabilities={"delivery": 0.1, "neighbour": 0.7, "sales": 0.2},
            confidence=0.7,
        )
    },
    expect={
        "other": [
            {
                "choice": "neighbour",
                "confidence": 0.7,
                "probabilities": {"delivery": 0.1, "neighbour": 0.7, "sales": 0.2},
            }
        ]
    },
)

CASES["pick_one_of_several:unsure"] = Case(
    states={"sensor.intercom_transcript": ("idle", {})},
    inputs=_PICK_ONE_INPUTS,
    fire=change("sensor.intercom_transcript", "Hard to make out"),
    answers={
        "answer": ChoiceAnswer(
            choice="sales",
            probabilities={"delivery": 0.3, "neighbour": 0.3, "sales": 0.4},
            confidence=0.4,
        )
    },
    expect={"no": [{"unsure": "yes"}]},
)


# ---------------------------------------------------------------------------
# score_ladder
# ---------------------------------------------------------------------------

_SCORE_LADDER_INPUTS = {
    "entities": ["sensor.backup_status"],
    "hold": NO_WAIT,
    "cooldown": NO_WAIT,
    "question": "How urgently must someone act on this?",
    "minimum_confidence": 0.5,
    "level_1_actions": run("no", level="{{ level }}"),
    "level_2_actions": [],
    "level_3_actions": run("other", level="{{ level }}", score="{{ score }}"),
    "level_4_actions": [],
    "unsure_actions": run("no", unsure="yes"),
}

_SCORE_LEGEND = {
    "0": "Nothing to do, it resolved itself",
    "1": "Worth looking at this week",
    "2": "Needs attention today",
    "3": "Wake someone up now",
}

CASES["score_ladder:match"] = Case(
    states={"sensor.backup_status": ("ok", {})},
    inputs=_SCORE_LADDER_INPUTS,
    fire=change("sensor.backup_status", "failed"),
    answers={
        "answer": ScoreAnswer(
            score=2.0,
            legend=_SCORE_LEGEND,
            probabilities={"0": 0.05, "1": 0.1, "2": 0.75, "3": 0.1},
            confidence=0.8,
        )
    },
    expect={"other": [{"level": "Needs attention today", "score": 2.0}]},
)

CASES["score_ladder:unsure"] = Case(
    states={"sensor.backup_status": ("ok", {})},
    inputs=_SCORE_LADDER_INPUTS,
    fire=change("sensor.backup_status", "unclear"),
    answers={
        "answer": ScoreAnswer(
            score=1.5,
            legend=_SCORE_LEGEND,
            probabilities={"0": 0.25, "1": 0.25, "2": 0.25, "3": 0.25},
            confidence=0.3,
        )
    },
    expect={"no": [{"unsure": "yes"}]},
)


# ---------------------------------------------------------------------------
# ask_before_acting
# ---------------------------------------------------------------------------

_ASK_BEFORE_ACTING_BASE = {
    "entities": ["sensor.driveway_camera"],
    "hold": NO_WAIT,
    "cooldown": NO_WAIT,
    "question": "Is there somebody at the door who needs an answer?",
    "high_threshold": 0.85,
    "low_threshold": 0.3,
    "timeout": {"minutes": 10},
    "actions": run("yes", p="{{ probability }}", confirmed="{{ confirmed }}"),
    "skipped_actions": run("no", p="{{ probability }}", confirmed="{{ confirmed }}"),
}


CASES["ask_before_acting:sure"] = Case(
    states={"sensor.driveway_camera": ("clear", {})},
    inputs={**_ASK_BEFORE_ACTING_BASE, "notify_device": "unused-device-id"},
    fire=change("sensor.driveway_camera", "person detected"),
    answers={"answer": NoulAnswer(noul=0.92)},
    expect={"yes": [{"p": 0.92, "confirmed": False}]},
)

CASES["ask_before_acting:skipped"] = Case(
    states={"sensor.driveway_camera": ("clear", {})},
    inputs={**_ASK_BEFORE_ACTING_BASE, "notify_device": "unused-device-id"},
    fire=change("sensor.driveway_camera", "a cat walked by"),
    answers={"answer": NoulAnswer(noul=0.1)},
    expect={"no": [{"p": 0.1, "confirmed": False}]},
)

# `device_attr()` only does `dr.async_get(hass).async_get(device_id)` and reads a
# plain attribute off whatever comes back (homeassistant/helpers/template/
# extensions/devices.py), so a stub with a `.name` is enough; no real mobile_app
# config entry or device registration is needed. The id is fixed ahead of time
# because `inputs` is a plain dict built before `hass` exists.
_NOTIFY_DEVICE_ID = "notify-test-phone"
_NOTIFY_DEVICE_STUB = SimpleNamespace(name="Phone")


def _confirm_notification(entity_id: str, new_state: str) -> Callable:
    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        registry = dr.async_get(hass)
        with patch.object(
            registry,
            "async_get",
            side_effect=lambda device_id: (
                _NOTIFY_DEVICE_STUB if device_id == _NOTIFY_DEVICE_ID else None
            ),
        ):
            calls = async_mock_service(hass, "notify", "mobile_app_phone")
            # `hold` is 0 for this case, so the state trigger fires and the
            # script runs up to `wait_for_trigger` inside the very next batch
            # of loop turns. A `await hass.async_block_till_done()` here would
            # wait for that same script task, which is now paused on a real
            # ten-minute timeout, so it would block for real wall-clock time
            # instead of returning. Poll bounded loop turns instead, just
            # long enough for the trigger and the notify action ahead of the
            # wait to run.
            hass.states.async_set(entity_id, new_state)
            for _ in range(200):
                await asyncio.sleep(0)
                if calls:
                    break
            else:
                raise AssertionError("the mid-band notification was never sent")
            action_id = calls[-1].data["data"]["actions"][0]["action"]
            hass.bus.async_fire("mobile_app_notification_action", {"action": action_id})
            # The event resolves the wait, so the script now runs to
            # completion (through the zero-length cooldown delay) and this
            # returns quickly.
            await hass.async_block_till_done()

    return fire


CASES["ask_before_acting:confirmed"] = Case(
    states={"sensor.driveway_camera": ("clear", {})},
    inputs={**_ASK_BEFORE_ACTING_BASE, "notify_device": _NOTIFY_DEVICE_ID},
    fire=_confirm_notification("sensor.driveway_camera", "somebody is standing there"),
    answers={"answer": NoulAnswer(noul=0.55)},
    expect={"yes": [{"p": 0.55, "confirmed": True}]},
)


# ---------------------------------------------------------------------------
# message_triage
# ---------------------------------------------------------------------------

_MESSAGE_TRIAGE_INPUTS = {
    "message_entity": "sensor.doorbell_transcript",
    "attribute": "",
    "hold": NO_WAIT,
    "cooldown": NO_WAIT,
    "needs_a_person_question": "Does a person need to do something about this?",
    "level_1_text": "Nothing to do, it resolved itself",
    "level_2_text": "Worth a look when you have a minute",
    "level_3_text": "Needs attention today",
    "level_4_text": "Wake someone up now",
    "urgent_actions": run(
        "yes", text="{{ text }}", urgency="{{ urgency }}", level="{{ level }}"
    ),
    "worth_a_look_actions": run("other", level="{{ level }}"),
    "ignored_actions": run("no", needs_a_person="{{ needs_a_person }}"),
}

CASES["message_triage:urgent"] = Case(
    states={"sensor.doorbell_transcript": ("idle", {})},
    inputs=_MESSAGE_TRIAGE_INPUTS,
    fire=change("sensor.doorbell_transcript", "There is smoke coming from the kitchen"),
    answers={
        "needs_a_person": NoulAnswer(noul=0.95),
        "urgency": ScoreAnswer(
            score=3.0,
            legend={
                "0": "Nothing to do, it resolved itself",
                "1": "Worth a look when you have a minute",
                "2": "Needs attention today",
                "3": "Wake someone up now",
            },
            probabilities={"0": 0.02, "1": 0.03, "2": 0.1, "3": 0.85},
            confidence=0.9,
        ),
    },
    expect={
        "yes": [
            {
                "text": "There is smoke coming from the kitchen",
                "urgency": 3.0,
                "level": "Wake someone up now",
            }
        ]
    },
)

CASES["message_triage:worth_a_look"] = Case(
    states={"sensor.doorbell_transcript": ("idle", {})},
    inputs=_MESSAGE_TRIAGE_INPUTS,
    fire=change("sensor.doorbell_transcript", "The delivery driver left a note"),
    answers={
        "needs_a_person": NoulAnswer(noul=0.6),
        "urgency": ScoreAnswer(
            score=2.0,
            legend={
                "0": "Nothing to do, it resolved itself",
                "1": "Worth a look when you have a minute",
                "2": "Needs attention today",
                "3": "Wake someone up now",
            },
            probabilities={"0": 0.1, "1": 0.2, "2": 0.6, "3": 0.1},
            confidence=0.7,
        ),
    },
    expect={"other": [{"level": "Needs attention today"}]},
)

CASES["message_triage:ignored"] = Case(
    states={"sensor.doorbell_transcript": ("idle", {})},
    inputs=_MESSAGE_TRIAGE_INPUTS,
    fire=change("sensor.doorbell_transcript", "A leaflet was pushed through the door"),
    answers={
        "needs_a_person": NoulAnswer(noul=0.05),
        "urgency": ScoreAnswer(
            score=0.0,
            legend={
                "0": "Nothing to do, it resolved itself",
                "1": "Worth a look when you have a minute",
                "2": "Needs attention today",
                "3": "Wake someone up now",
            },
            probabilities={"0": 0.9, "1": 0.05, "2": 0.03, "3": 0.02},
            confidence=0.85,
        ),
    },
    expect={"no": [{"needs_a_person": 0.05}]},
)


# ---------------------------------------------------------------------------
# llm_only_when_needed
# ---------------------------------------------------------------------------

_LLM_GATE_INPUTS = {
    "entities": ["sensor.security_event"],
    "hold": NO_WAIT,
    "cooldown": NO_WAIT,
    "question": "Is there anything here a person would actually want to be told about?",
    "true_means": "",
    "false_means": "",
    "background": "",
    "threshold": 0.7,
    "ai_task_entity": "ai_task.summarizer",
    "llm_instructions": "Write one short sentence describing what happened.",
    "actions": run("yes", message="{{ message }}", p="{{ probability }}"),
}


def _llm_gate_fire(entity_id: str, new_state: str) -> Callable:
    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        async_mock_service(
            hass,
            "ai_task",
            "generate_data",
            response={"data": "A parcel was left at the door."},
        )
        hass.states.async_set(entity_id, new_state)
        await hass.async_block_till_done()
        freezer.tick(timedelta(minutes=61))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

    return fire


CASES["llm_only_when_needed:yes"] = Case(
    states={"sensor.security_event": ("idle", {})},
    inputs=_LLM_GATE_INPUTS,
    fire=_llm_gate_fire("sensor.security_event", "person at the door"),
    answers={"answer": NoulAnswer(noul=0.8)},
    expect={"yes": [{"message": "A parcel was left at the door.", "p": 0.8}]},
)

CASES["llm_only_when_needed:no"] = Case(
    states={"sensor.security_event": ("idle", {})},
    inputs=_LLM_GATE_INPUTS,
    fire=_llm_gate_fire("sensor.security_event", "leaf blew past"),
    answers={"answer": NoulAnswer(noul=0.1)},
    expect={},
)


# ---------------------------------------------------------------------------
# situation_helper
# ---------------------------------------------------------------------------

_SITUATION_HELPER_INPUTS = {
    "entities": ["sensor.crowd_noise"],
    "hold": NO_WAIT,
    "cooldown": NO_WAIT,
    "recheck_hours": 0,
    "question": "Is a party happening right now?",
    "true_means": "",
    "false_means": "",
    "background": "",
    "helper": "input_boolean.party_mode",
    "on_threshold": 0.7,
    "off_threshold": 0.3,
}


def _situation_fire(
    entity_id: str, new_state: str, helper: str, initial: bool, expect_state: str
) -> Callable:
    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        object_id = helper.split(".", 1)[1]
        await async_setup_component(
            hass, "input_boolean", {"input_boolean": {object_id: {"initial": initial}}}
        )
        await hass.async_block_till_done()
        hass.states.async_set(entity_id, new_state)
        await hass.async_block_till_done()
        freezer.tick(timedelta(minutes=61))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        state = hass.states.get(helper)
        assert state is not None, f"{helper} was never created"
        assert state.state == expect_state

    return fire


CASES["situation_helper:turns_on"] = Case(
    states={"sensor.crowd_noise": ("quiet", {})},
    inputs=_SITUATION_HELPER_INPUTS,
    fire=_situation_fire(
        "sensor.crowd_noise", "loud", "input_boolean.party_mode", False, "on"
    ),
    answers={"answer": NoulAnswer(noul=0.9)},
    expect={},
)

CASES["situation_helper:turns_off"] = Case(
    states={"sensor.crowd_noise": ("loud", {})},
    inputs=_SITUATION_HELPER_INPUTS,
    fire=_situation_fire(
        "sensor.crowd_noise", "quiet", "input_boolean.party_mode", True, "off"
    ),
    answers={"answer": NoulAnswer(noul=0.1)},
    expect={},
)


# ---------------------------------------------------------------------------
# Changes that must not ask
# ---------------------------------------------------------------------------


def _never_runs(fire: Callable) -> Callable:
    """Fire, then check that the automation did not start at all.

    A run that fails on a blank or missing text also makes no call, but it logs an
    error and skips the cooldown. `last_triggered` is set only when a run starts,
    so it tells a clean skip from a failed call.
    """

    async def check(hass: HomeAssistant, freezer: Any) -> None:
        await fire(hass, freezer)
        await hass.async_block_till_done()
        (automation,) = hass.states.async_all("automation")
        assert automation.attributes["last_triggered"] is None, "the run started"

    return check


# Each blueprint that watches a list of entities, with inputs that point it at one
# of them. A state trigger with no `to:` or `not_to:` also fires when only an
# attribute changes (homeassistant/components/homeassistant/triggers/state.py, the
# `match_all` branch), and a sensor that drops out is not a new reading. Neither
# may spend a call.
_WATCHERS: dict[str, tuple[dict[str, Any], str, str]] = {
    "yes_no_question": (
        CASES["yes_no_question"].inputs,
        "sensor.washer_power",
        "1.2",
    ),
    "pick_one_of_several": (_PICK_ONE_INPUTS, "sensor.intercom_transcript", "idle"),
    "score_ladder": (_SCORE_LADDER_INPUTS, "sensor.backup_status", "ok"),
    "ask_before_acting": (
        {**_ASK_BEFORE_ACTING_BASE, "notify_device": "unused-device-id"},
        "sensor.driveway_camera",
        "clear",
    ),
    "llm_only_when_needed": (_LLM_GATE_INPUTS, "sensor.security_event", "idle"),
    "situation_helper": (_SITUATION_HELPER_INPUTS, "sensor.crowd_noise", "quiet"),
}

for _name, (_inputs, _entity, _value) in _WATCHERS.items():
    CASES[f"{_name}:attribute_only"] = Case(
        states={_entity: (_value, {"friendly_name": "Before"})},
        inputs=_inputs,
        fire=_never_runs(change(_entity, _value, friendly_name="After")),
        answers={"answer": NoulAnswer(noul=0.9)},
        expect={},
        asks=0,
    )
    CASES[f"{_name}:unavailable"] = Case(
        states={_entity: (_value, {})},
        inputs=_inputs,
        fire=_never_runs(change(_entity, "unavailable")),
        answers={"answer": NoulAnswer(noul=0.9)},
        expect={},
        asks=0,
    )


# ---------------------------------------------------------------------------
# message_triage: where the text comes from, and text that is not a message
# ---------------------------------------------------------------------------

_URGENT = {
    "needs_a_person": NoulAnswer(noul=0.95),
    "urgency": ScoreAnswer(
        score=3.0,
        legend={
            "0": "Nothing to do, it resolved itself",
            "1": "Worth a look when you have a minute",
            "2": "Needs attention today",
            "3": "Wake someone up now",
        },
        probabilities={"0": 0.02, "1": 0.03, "2": 0.1, "3": 0.85},
        confidence=0.9,
    ),
}


def _sent_text(text: str) -> Callable[..., None]:
    def check(state: Any, questions: dict[str, Any]) -> None:
        assert state == text

    return check


# Text in the state. None of these is a message, so none may be a paid call.
for _key, _fire in {
    "attribute_only": change("sensor.doorbell_transcript", "idle", heard="a knock"),
    "unavailable": change("sensor.doorbell_transcript", "unavailable"),
    "unknown": change("sensor.doorbell_transcript", "unknown"),
    "blank": change("sensor.doorbell_transcript", ""),
}.items():
    CASES[f"message_triage:{_key}"] = Case(
        states={"sensor.doorbell_transcript": ("idle", {})},
        inputs=_MESSAGE_TRIAGE_INPUTS,
        fire=_never_runs(_fire),
        answers=_URGENT,
        expect={},
        asks=0,
    )

_FROM_ATTRIBUTE = {**_MESSAGE_TRIAGE_INPUTS, "attribute": "message"}

CASES["message_triage:from_attribute"] = Case(
    states={"sensor.last_notification": ("on", {"message": "Your parcel is here"})},
    inputs={**_FROM_ATTRIBUTE, "message_entity": "sensor.last_notification"},
    fire=change(
        "sensor.last_notification", "on", message="There is smoke in the kitchen"
    ),
    answers=_URGENT,
    expect={
        "yes": [
            {
                "text": "There is smoke in the kitchen",
                "urgency": 3.0,
                "level": "Wake someone up now",
            }
        ]
    },
    check_request=_sent_text("There is smoke in the kitchen"),
)

# With an attribute chosen, the state and every other attribute are not the text.
for _key, _fire in {
    "state_only": change(
        "sensor.last_notification", "off", message="Your parcel is here"
    ),
    "other_attribute": change(
        "sensor.last_notification", "on", message="Your parcel is here", app="mail"
    ),
    "cleared": change("sensor.last_notification", "on"),
    "blank_attribute": change("sensor.last_notification", "on", message=""),
}.items():
    CASES[f"message_triage:attribute_{_key}"] = Case(
        states={"sensor.last_notification": ("on", {"message": "Your parcel is here"})},
        inputs={**_FROM_ATTRIBUTE, "message_entity": "sensor.last_notification"},
        fire=_never_runs(_fire),
        answers=_URGENT,
        expect={},
        asks=0,
    )


# ---------------------------------------------------------------------------
# ask_before_acting: nobody taps, then an old notification is tapped
# ---------------------------------------------------------------------------


async def _until(condition: Callable[[], bool], what: str) -> None:
    """Run bounded loop turns until `condition` holds.

    `hass.async_block_till_done()` would wait for the script paused in
    `wait_for_trigger`, which is the hang `_confirm_notification` describes.
    """
    for _ in range(200):
        await asyncio.sleep(0)
        if condition():
            return
    raise AssertionError(what)


def _time_out_then_tap_the_old_one(entity_id: str) -> Callable:
    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        registry = dr.async_get(hass)
        with patch.object(
            registry,
            "async_get",
            side_effect=lambda device_id: (
                _NOTIFY_DEVICE_STUB if device_id == _NOTIFY_DEVICE_ID else None
            ),
        ):
            calls = async_mock_service(hass, "notify", "mobile_app_phone")

            # First run: the question goes out and nobody answers.
            hass.states.async_set(entity_id, "somebody is standing there")
            await _until(lambda: len(calls) == 1, "the first question was never sent")
            first = calls[0].data["data"]["actions"][0]["action"]
            assert calls[0].data["data"]["tag"] == first
            freezer.tick(timedelta(minutes=11))
            async_fire_time_changed(hass, dt_util.utcnow())
            # The wait has timed out, so nothing is paused and this returns.
            await hass.async_block_till_done()
            # The unanswered question is taken off the phone.
            assert len(calls) == 2, "the timed out question was not cleared"
            assert calls[1].data["message"] == "clear_notification"
            assert calls[1].data["data"]["tag"] == first

            # Second run: a tap on the first question must not answer this one.
            hass.states.async_set(entity_id, "still standing there")
            await _until(lambda: len(calls) == 3, "the second question was never sent")
            assert calls[2].data["data"]["actions"][0]["action"] != first
            hass.bus.async_fire("mobile_app_notification_action", {"action": first})
            for _ in range(50):
                await asyncio.sleep(0)
            freezer.tick(timedelta(minutes=11))
            async_fire_time_changed(hass, dt_util.utcnow())
            await hass.async_block_till_done()

    return fire


CASES["ask_before_acting:timed_out"] = Case(
    states={"sensor.driveway_camera": ("clear", {})},
    inputs={**_ASK_BEFORE_ACTING_BASE, "notify_device": _NOTIFY_DEVICE_ID},
    fire=_time_out_then_tap_the_old_one("sensor.driveway_camera"),
    answers={"answer": NoulAnswer(noul=0.55)},
    expect={},
    asks=2,
)


# ---------------------------------------------------------------------------
# llm_only_when_needed: what the AI task is told, and what happens when it fails
# ---------------------------------------------------------------------------


def _llm_gate_checked(entity_id: str, new_state: str, *, fails: bool = False) -> Callable:
    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        calls = async_mock_service(
            hass,
            "ai_task",
            "generate_data",
            response={"data": "A parcel was left at the door."},
            supports_response=SupportsResponse.ONLY,
            raise_exception=HomeAssistantError("provider down") if fails else None,
        )
        hass.states.async_set(entity_id, new_state, {"friendly_name": "Driveway"})
        await hass.async_block_till_done()
        (call,) = calls
        # The instructions alone say nothing about what happened, so the task is
        # also given the question and the states Jev judged.
        instructions = call.data["instructions"]
        assert instructions.startswith(_LLM_GATE_INPUTS["llm_instructions"])
        assert _LLM_GATE_INPUTS["question"] in instructions
        assert f"Driveway: {new_state}" in instructions

    return fire


CASES["llm_only_when_needed:ai_task_told"] = Case(
    states={"sensor.security_event": ("idle", {"friendly_name": "Driveway"})},
    inputs=_LLM_GATE_INPUTS,
    fire=_llm_gate_checked("sensor.security_event", "person at the door"),
    answers={"answer": NoulAnswer(noul=0.8)},
    expect={"yes": [{"message": "A parcel was left at the door.", "p": 0.8}]},
)

# The gate said a person wants to know, so a failed AI task still tells them, in
# plain words, instead of ending the run before the cooldown.
CASES["llm_only_when_needed:ai_task_fails"] = Case(
    states={"sensor.security_event": ("idle", {"friendly_name": "Driveway"})},
    inputs=_LLM_GATE_INPUTS,
    fire=_llm_gate_checked("sensor.security_event", "person at the door", fails=True),
    answers={"answer": NoulAnswer(noul=0.8)},
    expect={"yes": [{"message": "Driveway: person at the door", "p": 0.8}]},
)


# ---------------------------------------------------------------------------
# situation_helper: the recheck hour, swapped thresholds, and its own helper
# ---------------------------------------------------------------------------


def _situation_at(hour: int, helper: str, expect_state: str) -> Callable:
    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        object_id = helper.split(".", 1)[1]
        await async_setup_component(
            hass, "input_boolean", {"input_boolean": {object_id: {"initial": False}}}
        )
        await hass.async_block_till_done()
        await at(hour)(hass, freezer)
        await hass.async_block_till_done()
        assert hass.states.get(helper).state == expect_state

    return fire


# Every 6 hours means at 0, 6, 12 and 18 o'clock, and at no other hour.
CASES["situation_helper:recheck_due"] = Case(
    states={"sensor.crowd_noise": ("loud", {})},
    inputs={**_SITUATION_HELPER_INPUTS, "recheck_hours": 6},
    fire=_situation_at(18, "input_boolean.party_mode", "on"),
    answers={"answer": NoulAnswer(noul=0.9)},
    expect={},
)

CASES["situation_helper:recheck_not_due"] = Case(
    states={"sensor.crowd_noise": ("loud", {})},
    inputs={**_SITUATION_HELPER_INPUTS, "recheck_hours": 6},
    fire=_situation_at(19, "input_boolean.party_mode", "off"),
    answers={"answer": NoulAnswer(noul=0.9)},
    expect={},
    asks=0,
)

CASES["situation_helper:recheck_off"] = Case(
    states={"sensor.crowd_noise": ("loud", {})},
    inputs=_SITUATION_HELPER_INPUTS,
    fire=_situation_at(0, "input_boolean.party_mode", "off"),
    answers={"answer": NoulAnswer(noul=0.9)},
    expect={},
    asks=0,
)

# An "on" below the "off" would turn the helper on and off in one run for any
# answer between them. The UI sliders cannot do this, YAML can. The run refuses
# before it pays for a call.
CASES["situation_helper:thresholds_swapped"] = Case(
    states={"sensor.crowd_noise": ("quiet", {})},
    inputs={**_SITUATION_HELPER_INPUTS, "on_threshold": 0.3, "off_threshold": 0.6},
    fire=_situation_fire(
        "sensor.crowd_noise", "loud", "input_boolean.party_mode", False, "off"
    ),
    answers={"answer": NoulAnswer(noul=0.45)},
    expect={},
    asks=0,
)


def _flip_the_helper(helper: str) -> Callable:
    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        object_id = helper.split(".", 1)[1]
        await async_setup_component(
            hass, "input_boolean", {"input_boolean": {object_id: {"initial": False}}}
        )
        await hass.async_block_till_done()
        await hass.services.async_call(
            "input_boolean", "turn_on", {"entity_id": helper}, blocking=True
        )
        await hass.async_block_till_done()
        freezer.tick(timedelta(minutes=61))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

    return fire


# Watching the helper lets Jev see the current mode, but the helper changing
# because this blueprint (or a person) set it is not news about the situation.
CASES["situation_helper:own_helper_changes"] = Case(
    states={"sensor.crowd_noise": ("loud", {})},
    inputs={
        **_SITUATION_HELPER_INPUTS,
        "entities": ["sensor.crowd_noise", "input_boolean.party_mode"],
    },
    fire=_flip_the_helper("input_boolean.party_mode"),
    answers={"answer": NoulAnswer(noul=0.1)},
    expect={},
    asks=0,
)


# ---------------------------------------------------------------------------
# pick_one_of_several: blank options are not offered
# ---------------------------------------------------------------------------


def _offered(expected: dict[str, Any]) -> Callable[..., None]:
    def check(state: Any, questions: dict[str, Any]) -> None:
        assert dict(questions["answer"].criteria) == expected

    return check


# Options 3 and 4 left blank, one of them with a stray space. Jev sees two
# options, and the actions under the blank ones never run.
CASES["pick_one_of_several:two_options"] = Case(
    states={"sensor.intercom_transcript": ("idle", {})},
    inputs={
        **_PICK_ONE_INPUTS,
        "option_1_name": "delivery ",
        "option_3_name": " ",
        "option_3_description": "",
        "option_3_actions": run("no", ran="3"),
        "option_4_actions": run("no", ran="4"),
    },
    fire=change("sensor.intercom_transcript", "Package for number 31"),
    answers={
        "answer": ChoiceAnswer(
            choice="delivery",
            probabilities={"delivery": 0.8, "neighbour": 0.2},
            confidence=0.8,
        )
    },
    expect={"yes": [{"choice": "delivery"}]},
    check_request=_offered(
        {
            "delivery": "A parcel or food delivery",
            "neighbour": "Somebody who lives nearby",
        }
    ),
)
