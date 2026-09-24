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
from dataclasses import dataclass, field
from typing import Any

from homeassistant.helpers import intent as ha_intent
from jevclient import Choice, ChoiceAnswer, JevResponse, Noul, NoulAnswer, Question

from .snapshot import ExposedEntity, HomeSnapshot

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

# The words that turn a number into a percentage, in the languages the integration
# is translated into. "%" carries most of the traffic; these are for a satellite
# that transcribes the word instead of the sign.
_PERCENT_WORDS = (
    "%",
    r"per ?cento?",  # en, and it "per cento"
    r"procent\w*",  # nl, sv, da, pl, cs
    "prozent",  # de
    r"pour ?cent\w*",  # fr
    "por ?ciento",  # es
    "por ?cento",  # pt-BR
    r"процент\w*",  # ru
)
# The lookarounds keep a number whole: "1000 percent" and "12.5 percent" are not
# brightnesses, and without them the regex found 0 and 5 inside them.
_NUMBER = r"(?<![\d.,])(\d{1,3})(?![\d.,]\d)"
_PERCENT = re.compile(_NUMBER + r"\s*(?:" + "|".join(_PERCENT_WORDS) + ")", re.IGNORECASE)
# Chinese writes its marker in front of the number instead of after it.
_PERCENT_PREFIX = re.compile(r"百分之\s*" + _NUMBER)
# Digit lookarounds rather than \b, because Chinese writes no space in front of
# the number and \b never fires between two characters that are both word
# characters. "\u628a\u706f\u8c03\u6697\u523030" has to give 30.
_BARE_NUMBER = re.compile(_NUMBER)
# "20% brighter" and "dim it by 20" change the level by an amount. HassLightSet only
# sets a level, so these go to the fallback agent rather than being read as 20%.
_RELATIVE = re.compile(
    r"\b(?:brighter|dimmer|darker)\b"
    r"|\b(?:by|met|um)\s+\d",
    re.IGNORECASE,
)

# A bare number becomes a brightness only when the sentence also says something
# about light level. The model already chose set_brightness by this point, so this
# is a guard against "turn on 2 lamps", not a classifier. Stems, matched at a word
# start.
_LEVEL_STEMS = {
    "en": ("bright", "dim"),
    "nl": ("helder",),
    "de": ("hell(?!o)", "dunkel"),  # the guard keeps "hello" out of the English path
    "fr": ("luminos", "tamis", "clair", "sombre"),
    "it": ("luminos", "attenua", "chiar", "scur"),
    "es": ("brill", "atenu", "atenú", "oscur"),
    "pt-BR": ("brilh", "escur"),
    "pl": ("jasn", "przyciemn"),
    "sv": ("ljus", "dämp"),
    "da": ("lys", "dæmp"),
    "cs": ("jas", "ztlum", "stmív"),
    "ru": ("ярк", "приглуш", "свет"),
}
_LEVEL = re.compile(
    r"\b(?:" + "|".join(s for g in _LEVEL_STEMS.values() for s in g) + ")",
    re.IGNORECASE,
)
# No spaces in Chinese, so a word boundary never fires in front of these.
_LEVEL_CJK = ("亮", "暗")


def _in_range(raw: str) -> int | None:
    value = int(raw)
    return value if 0 <= value <= 100 else None


def find_brightness(text: str) -> int | None:
    """A percentage in the text, if there is one.

    Prefers an explicit percent sign, because "turn on 2 lamps" holds a number that
    is not a brightness. Without one, the last number wins, because a device name
    comes before its level: "lamp 2 brightness to 40" means 40.
    """
    if _RELATIVE.search(text):
        return None
    if m := _PERCENT.search(text):
        return _in_range(m.group(1))
    if m := _PERCENT_PREFIX.search(text):
        return _in_range(m.group(1))
    if _LEVEL.search(text) or any(word in text for word in _LEVEL_CJK):
        if numbers := _BARE_NUMBER.findall(text):
            return _in_range(numbers[-1])
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
    action_probabilities: dict[str, float] = field(default_factory=dict)
    # (entity name, the state it is already in) when there is nothing left to do.
    already_satisfied: tuple[str, str] | None = None
    # Two entity ids the command could mean, when the agent should ask which.
    candidates: tuple[str, str] | None = None

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

    questions: dict[str, Question] = {
        "action": Choice(
            "What should happen?",
            {
                # No lock wording here on purpose. The agent does not control
                # locks, and Home Assistant's on/off convention for them runs the
                # opposite way round from speech. See CONTROLLABLE in snapshot.py.
                "turn_on": "Switch something on, open it, start it, "
                "or run a script or scene",
                "turn_off": "Switch something off, close it, or stop it",
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
                # Answered in the same request as action, so it must fit a
                # status check too.
                "question": "Which device is this about?",
                "background": "Match on the name and on the room. Pick "
                "none_of_these when no single device is meant.",
            },
            entity_options,
        ),
    }
    # A house can have exposed entities and no areas at all, which left this one
    # question holding nothing but none_of_these. jevclient rejects a one-option
    # choice, so the whole command used to raise ValueError. Ask only when there is
    # a room to name; interpret() already treats a missing area answer as no area.
    if snapshot.areas:
        area_options: dict[str, Any] = dict.fromkeys(snapshot.areas)
        area_options[NONE] = "No room is named"
        questions["area"] = Choice("Which room is meant?", area_options)
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
    *,
    ask_back: bool = True,
    heard_in: str | None = None,
) -> Interpretation:
    """Read the answers that matter and ignore the rest.

    ask_back=False reads a command whose device a reply has already picked.
    heard_in is the area id of the satellite or device that heard the command.
    """

    def choice(key: str) -> ChoiceAnswer | None:
        answer = response.answers.get(key)
        return answer if isinstance(answer, ChoiceAnswer) else None

    def noul(key: str) -> float:
        answer = response.answers.get(key)
        return answer.noul if isinstance(answer, NoulAnswer) else 0.0

    def out(reason: str) -> Interpretation:
        # The action answer goes into the trace even on a fallback, because "action
        # confidence 0.31" is only readable next to which action scored it.
        action = choice("action")
        return Interpretation(
            None,
            {},
            action.choice if action else "",
            action.confidence if action else 0.0,
            reason,
            fallback=True,
            action_probabilities=dict(action.probabilities or {}) if action else {},
        )

    # Either one answered the wrong way means acting on part of the sentence, and a
    # fallback costs only a slower answer. So both refuse at even odds. Neither
    # number has a measurement behind it yet.
    if noul("compound") >= 0.5:
        return out("several commands in one sentence")
    if noul("free_text") >= 0.5:
        return out("needs text written or looked up")

    action = choice("action")
    entity = choice("entity")
    if action is None or action.choice == NONE:
        return out("not a house command")
    if action.confidence < min_confidence:
        # A command that is already done reads as a low-confidence one.
        #
        # Measured on a real instance, three runs per starting state: "could you put
        # the desk lamp on please" scored the action at 1.00 with the lamp off and
        # 0.25 to 0.31 with it on. turn_on stayed the top option at 0.39 to 0.48 and
        # the rest went to get_state, because with the lamp already on the sentence
        # really could be either. Refusing that as not understood is the wrong
        # answer to a sentence the model read correctly.
        if settled := _already_done(action, entity, snapshot, min_confidence):
            return Interpretation(
                None,
                {},
                action.choice,
                action.confidence,
                "already satisfied",
                fallback=False,
                already_satisfied=settled,
                action_probabilities=dict(action.probabilities or {}),
            )
        return out(
            f"action confidence {action.confidence:.2f} below {min_confidence:.2f}"
        )

    intent_type = ACTIONS[action.choice]
    target = choice("target_type")
    area = choice("area")
    slots: dict[str, Any] = {}
    targets_everything = False
    named_area = (
        area.choice
        if area is not None and area.choice != NONE and area.confidence >= min_confidence
        else None
    )

    def ask(first: ExposedEntity, second: ExposedEntity) -> Interpretation:
        # The action is sure and the device is one of two. Asking costs one short
        # question, and handing the sentence to the fallback agent gets the same
        # guess made again by something that does not know it was a guess.
        if spoken_name(first, second) is None:
            return out("two devices fit the name and nothing tells them apart")
        return Interpretation(
            None,
            {},
            action.choice,
            action.confidence,
            "two devices fit the name",
            fallback=False,
            action_probabilities=dict(action.probabilities or {}),
            candidates=(first.entity_id, second.entity_id),
        )

    def pick(chosen: ExposedEntity, *, sure: bool) -> ExposedEntity | Interpretation:
        # sure is False when the model put most of its answer on none. Then only a
        # name that two devices share is a reason to go on.
        tied = _fit_as_well(text, chosen, snapshot, named_area)
        if not sure and len(tied) < 2:
            return out("no target named with enough confidence")
        # A name that fits two devices is settled by the room it was said in, as
        # Home Assistant's own agent settles it. A room the command names came first.
        here = [e for e in tied if heard_in is not None and e.area_id == heard_in]
        if len(tied) > 1 and len(here) == 1:
            return here[0]
        if len(tied) == 1:
            return tied[0]
        if len(tied) == 2:
            return ask(*tied)
        return out(f"{len(tied)} devices fit the name")

    # Trust the confident answer rather than the ordering. Measured: a scope answer
    # of one_room at 0.41 alongside a device answer at 1.00, where branching on
    # scope first threw away the certain answer and acted on the whole house.
    described: ExposedEntity | None = None
    if (
        entity is not None
        and entity.choice != NONE
        and entity.confidence >= min_confidence
    ):
        described = snapshot.by_id(entity.choice)
        if described is None:
            return out("named a device that is not exposed")
        if ask_back:
            picked = pick(described, sure=True)
            if isinstance(picked, Interpretation):
                return picked
            described = picked
    elif area is not None and area.choice != NONE and area.confidence >= min_confidence:
        slots["area"] = {"value": area.choice}
    elif (
        target is not None
        and target.choice == "everything"
        and target.confidence >= min_confidence
    ):
        # Home Assistant requires one of name, area or floor, and reads the literal
        # name "all" as every entity, clearing it after the check. Sending no target
        # at all failed that check on a real instance: "turn everything off"
        # answered "Sorry, that did not work" while the model had it right at 0.99.
        #
        # "all" still needs a domain beside it. Home Assistant refuses a bare one
        # with "Service handler cannot target all devices", and it is right to: an
        # unbounded off is not something to infer from one ambiguous sentence.
        slots["name"] = {"value": "all"}
        targets_everything = True
    elif (
        ask_back
        and entity is not None
        and (unsure := snapshot.by_id(_likeliest_device(entity))) is not None
    ):
        picked = pick(unsure, sure=False)
        if isinstance(picked, Interpretation):
            return picked
        described = picked
    else:
        return out("no target named with enough confidence")

    if described is not None:
        slots["name"] = {"value": described.name}
        # The domain keeps a same-named entity the model was never shown, a lock
        # called "Front door" beside a cover called "Front door", out of the match.
        slots["domain"] = {"value": [described.domain]}
        if described.area_id:
            slots["preferred_area_id"] = {"value": described.area_id}

    # An area always carries a domain. With none, Home Assistant acts on every
    # exposed entity in the room whatever its domain, so turn_off on a hallway with a
    # light and a lock unlocked the lock. Without a confident answer, the domains the
    # model was shown are the bound. The whole house takes that default only when the
    # model was shown one kind of device, otherwise the agent asks which kind.
    domain = choice("domain")
    if "domain" not in slots:
        if (
            domain is not None
            and domain.choice != NONE
            and domain.confidence >= min_confidence
        ):
            slots["domain"] = {"value": [domain.choice]}
        elif not targets_everything or len(snapshot.domains) == 1:
            slots["domain"] = {"value": snapshot.domains}

    if action.choice == "set_brightness":
        brightness = find_brightness(text)
        if brightness is None:
            return out("a brightness was asked for but no number was said")
        slots["brightness"] = {"value": brightness}
        slots["domain"] = {"value": ["light"]}

    return Interpretation(
        intent_type=intent_type,
        slots=slots,
        action=action.choice,
        confidence=action.confidence,
        reason="ok",
        fallback=False,
        targets_everything=targets_everything,
    )


def _likeliest_device(entity: ChoiceAnswer) -> str:
    """The device the model gave most of its answer to, even if "none" got more.

    Measured on a development instance with two lights both called "Lamp": "turn on
    the lamp" came back as none_of_these 0.55, one Lamp 0.44 and the other 0.01. The
    model split its answer because the name was shared, so the name decides.
    """
    devices = {k: v for k, v in (entity.probabilities or {}).items() if k != NONE}
    if entity.choice != NONE or not devices:
        return entity.choice
    return max(devices, key=lambda k: devices[k])


def _fit_as_well(
    text: str, chosen: ExposedEntity, snapshot: HomeSnapshot, area: str | None
) -> list[ExposedEntity]:
    """The devices of the chosen kind whose names fit the words as well as its own.

    The model gives one device all of its answer even when two fit: measured on a
    development instance with two lights both called "Lamp", "turn on the lamp"
    came back as one of them at 1.00, every time. The probabilities cannot say the
    name was shared, the names can. A room the command names narrows the list.

    Only the chosen device when it fits best alone, or when no name fits the words
    at all, which is where the model's reading is all there is.
    """
    kind = [
        e
        for e in snapshot.entities
        if e.domain == chosen.domain and (area is None or e.area == area)
    ]
    if chosen not in kind:
        return [chosen]
    fit = {e.entity_id: _name_fit(text, e.name) for e in kind}
    best = max(fit.values())
    if best == 0 or fit[chosen.entity_id] < best:
        return [chosen]
    return [e for e in kind if fit[e.entity_id] == best]


def _name_fit(text: str, name: str) -> int:
    """How well the words fit a name. The whole name said beats any part of it."""
    if said := _words_said(text, name):
        return 100 + said
    words = set(re.findall(r"\w+", text.casefold()))
    return len(set(name.casefold().split()) & words)


def spoken_name(one: ExposedEntity, other: ExposedEntity) -> tuple[str, str] | None:
    """How to say the two apart: by name, or by room when the names are the same.

    None when neither tells them apart, since "the fan or the fan" asks nothing.
    """
    if one.name.casefold() != other.name.casefold():
        return one.name, other.name
    if one.area and other.area and one.area.casefold() != other.area.casefold():
        return f"{one.name} ({one.area})", f"{other.name} ({other.area})"
    return None


def _words_said(text: str, name: str) -> int:
    """How many words of the name the text says, as one phrase, or 0.

    Whole words only, so a hidden "Lamp" is not found inside "lamps". In a language
    written without spaces a name rarely stands apart, so the check seldom fires.
    """
    words = name.casefold().split()
    if not words:
        return 0
    phrase = r"\s+".join(re.escape(w) for w in words)
    return len(words) if re.search(rf"(?<!\w){phrase}(?!\w)", text.casefold()) else 0


# What "already done" looks like for each action the check covers.
_SETTLED = {"turn_on": "on", "turn_off": "off"}


def _already_done(
    action: ChoiceAnswer,
    entity: ChoiceAnswer | None,
    snapshot: HomeSnapshot,
    min_confidence: float,
) -> tuple[str, str] | None:
    """What is already true, when a command would change nothing.

    Reads the top option rather than the winning one, because a redundant command
    spreads its probability without moving the ranking.
    """
    if entity is None or entity.choice == NONE or entity.confidence < min_confidence:
        return None
    if not action.probabilities:
        return None
    top = max(action.probabilities, key=lambda k: action.probabilities[k])
    wanted = _SETTLED.get(top)
    if wanted is None:
        return None
    described = snapshot.by_id(entity.choice)
    if described is None or described.state != wanted:
        return None
    return described.name, wanted
