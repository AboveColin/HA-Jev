"""Turn one spoken command into a Home Assistant intent.

Two rules shape this file, both measured rather than assumed.

Numbers are pulled out in code, never asked for. Jev judges and does not calculate,
and asking it to read "set the lamp to 40 percent" as a number separated cases by
0.06 where doing the comparison first gave 0.69. A regex is exact and free.

Every question the router could need goes in one request, including the five or so
that will be discarded. Three questions took 712 ms and a hundred took 714, so the
alternative, a chain of calls each waiting on the last, is slower and costs more.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from homeassistant.helpers import intent as ha_intent
from jevclient import Choice, ChoiceAnswer, JevResponse, Noul, NoulAnswer, Question

from .snapshot import HomeSnapshot

NONE = "none_of_these"

# Every action the router can take, and the intent each one runs. Anything absent
# goes to the fallback agent rather than being approximated here.
ACTIONS: dict[str, str] = {
    "turn_on": ha_intent.INTENT_TURN_ON,
    "turn_off": ha_intent.INTENT_TURN_OFF,
    "toggle": ha_intent.INTENT_TOGGLE,
    "set_brightness": "HassLightSet",
    "get_state": ha_intent.INTENT_GET_STATE,
}

_PERCENT = re.compile(r"(\d{1,3})\s*(?:%|percent|procent)")
_BARE_NUMBER = re.compile(r"\b(\d{1,3})\b")


def find_brightness(text: str) -> int | None:
    """A percentage in the text, if there is one.

    Prefers an explicit percent sign, because "turn on 2 lamps" holds a number that
    is not a brightness.
    """
    if m := _PERCENT.search(text):
        value = int(m.group(1))
        return value if 0 <= value <= 100 else None
    if "bright" in text.lower() or "dim" in text.lower():
        if m := _BARE_NUMBER.search(text):
            value = int(m.group(1))
            return value if 0 <= value <= 100 else None
    return None


@dataclass(slots=True)
class Interpretation:
    """What the router decided, and why, so a trace can be read afterwards."""

    intent_type: str | None
    slots: dict[str, Any]
    action: str
    confidence: float
    reason: str
    fallback: bool
    targets_everything: bool = False

    @property
    def should_fall_back(self) -> bool:
        return self.fallback or self.intent_type is None


def build_questions(
    text: str, snapshot: HomeSnapshot, max_options: int
) -> dict[str, Question]:
    """One request, every question the router could need.

    `background` carries the standing rules rather than the state, because the same
    sentence measured about twice as useful attached to the question as attached to
    the readings.
    """
    entity_options: dict[str, Any] = {
        e.entity_id: e.as_option() for e in snapshot.entities[:max_options]
    }
    entity_options[NONE] = "The command does not name one particular device"

    area_options: dict[str, Any] = dict.fromkeys(snapshot.areas)
    area_options[NONE] = "No room is named"

    questions: dict[str, Question] = {
        "action": Choice(
            "What should happen?",
            {
                "turn_on": "Switch something on, open it, start it, or unlock it",
                "turn_off": "Switch something off, close it, stop it, or lock it",
                "toggle": "Flip whatever state it is in now",
                "set_brightness": "Change how bright a light is",
                "get_state": "Answer a question about the current state, "
                "changing nothing",
                NONE: "None of these, or the request is not about the house",
            },
        ),
        "compound": Noul(
            "Does this request contain more than one distinct command?",
            true="Two or more separate things are being asked for",
            false="A single instruction, however it is phrased",
        ),
        "free_text": Noul(
            {
                "question": "Does answering this need words to be written or "
                "repeated back?",
                "examples": [
                    "add milk to the shopping list",
                    "broadcast that dinner is ready",
                    "what is the capital of France",
                ],
            },
            true="It needs text written, quoted or looked up",
            false="It is a device command or a question about device state",
        ),
        "target_type": Choice(
            "How is the target named?",
            {
                "entity": "One particular device is named",
                "area": "A room or area is named, covering what is in it",
                "everything": "The whole house, with no room or device named",
                NONE: "No target is named at all",
            },
        ),
        "entity": Choice(
            {
                "question": "Which device should receive this?",
                "background": "Match on the name and on the room. Pick "
                "none_of_these when no single device is meant.",
            },
            entity_options,
        ),
        "area": Choice("Which room is meant?", area_options),
    }
    if len(snapshot.domains) >= 2:
        questions["domain"] = Choice(
            "Which kind of device is meant?",
            dict.fromkeys(snapshot.domains) | {NONE: "No particular kind"},
        )
    return questions


def interpret(
    response: JevResponse,
    text: str,
    snapshot: HomeSnapshot,
    min_confidence: float,
) -> Interpretation:
    """Read the answers that matter and ignore the rest."""

    def choice(key: str) -> ChoiceAnswer | None:
        answer = response.answers.get(key)
        return answer if isinstance(answer, ChoiceAnswer) else None

    def noul(key: str) -> float:
        answer = response.answers.get(key)
        return answer.noul if isinstance(answer, NoulAnswer) else 0.0

    def out(reason: str) -> Interpretation:
        return Interpretation(None, {}, "", 0.0, reason, fallback=True)

    if noul("compound") > 0.8:
        return out("several commands in one sentence")
    if noul("free_text") > 0.6:
        return out("needs text written or looked up")

    action = choice("action")
    if action is None or action.choice == NONE:
        return out("not a house command")
    if action.confidence < min_confidence:
        return out(
            f"action confidence {action.confidence:.2f} below {min_confidence:.2f}"
        )

    intent_type = ACTIONS[action.choice]
    target = choice("target_type")
    entity = choice("entity")
    area = choice("area")
    slots: dict[str, Any] = {}
    targets_everything = False

    # Trust the confident answer rather than the ordering. Measured: a scope answer
    # of one_room at 0.41 alongside a device answer at 1.00, where branching on
    # scope first threw away the certain answer and acted on the whole house.
    if (
        entity is not None
        and entity.choice != NONE
        and entity.confidence >= min_confidence
    ):
        described = snapshot.by_id(entity.choice)
        if described is None:
            return out("named a device that is not exposed")
        slots["name"] = {"value": described.name}
        if described.area:
            slots["preferred_area_id"] = {"value": described.area}
    elif area is not None and area.choice != NONE and area.confidence >= min_confidence:
        slots["area"] = {"value": area.choice}
    elif target is not None and target.choice == "everything":
        targets_everything = True
    else:
        return out("no target named with enough confidence")

    domain = choice("domain")
    if (
        domain is not None
        and domain.choice != NONE
        and domain.confidence >= min_confidence
    ):
        slots["domain"] = {"value": [domain.choice]}

    if action.choice == "set_brightness":
        brightness = find_brightness(text)
        if brightness is None:
            return out("a brightness was asked for but no number was said")
        slots["brightness"] = {"value": brightness}
        slots.setdefault("domain", {"value": ["light"]})

    return Interpretation(
        intent_type=intent_type,
        slots=slots,
        action=action.choice,
        confidence=action.confidence,
        reason="ok",
        fallback=False,
        targets_everything=targets_everything,
    )
