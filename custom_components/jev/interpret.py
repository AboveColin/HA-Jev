"""Turn one spoken command into a Home Assistant intent.

Two rules shape this file, both measured rather than assumed.

Digits are pulled out in code. Jev judges and does not calculate, and asking it to
read "set the lamp to 40 percent" as a number separated cases by 0.06 where doing
the comparison first gave 0.69. A regex is exact and free. A level said in words,
"forty percent" or "half", has no digit to read, and only then does a second
request ask for it.

Every question the router could need goes in one request, including the five or so
that will be discarded. Three questions took 712 ms and a hundred took 714, so the
alternative, a chain of calls each waiting on the last, is slower and costs more.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.helpers import intent as ha_intent
from jevclient import (
    Choice,
    ChoiceAnswer,
    JevResponse,
    Noul,
    NoulAnswer,
    Question,
    Score,
    ScoreAnswer,
)

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
# is translated into, and in Hungarian. "%" carries most of the traffic; these are
# for a satellite that transcribes the word instead of the sign.
_PERCENT_WORDS = (
    "%",
    r"per ?cento?",  # en, and it "per cento"
    r"procent\w*",  # nl, sv, da, pl, cs
    "prozent",  # de
    r"pour ?cent\w*",  # fr
    "por ?ciento",  # es
    "por ?cento",  # pt-BR
    r"процент\w*",  # ru
    r"százalék\w*",  # hu
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
# A number can be the level to set or the amount to change it by. HassLightSet only
# sets a level, so an amount goes to the fallback agent rather than being read as
# the level: "turn it up 20%" on a light at 60% set it to 20. When the words are
# unclear the number is not read, because a fallback costs a sentence and a wrong
# level turns the room dark.
#
# Words in front of the number that make it the level: "to 20%", "auf 20".
_TO = re.compile(
    r"(?:\b(?:to|at|auf|zu|op|naar|tot|à|a|au|al|allo|alla|para|na|do|på|till|til|до)"
    r"|到|为|成|至)\s*$",
    re.IGNORECASE,
)
# Words in front of the number that make it an amount: "by 20%", "um 20". Russian
# "на", Portuguese "em" and Spanish "en" mean both, so the change words below decide.
_BY = re.compile(
    r"\b(?:by|um|met|de|del|di|un|o|med)\s*$",
    re.IGNORECASE,
)
# Hungarian puts "to" and "by" on the number as a suffix: "20%-ra", "20%-kal".
_TO_SUFFIX = re.compile(r"^(?:\s*százalék)?-?(?:ra|re)\b", re.IGNORECASE)
_BY_SUFFIX = re.compile(r"^(?:\s*százalék)?-?(?:kal|kel)\b", re.IGNORECASE)
# Words anywhere in the sentence that ask for a change rather than a level. They
# count only when no "to" stands in front of the number, so "turn it up to 50%" is
# still 50. Stems, matched at a word start.
_CHANGE_STEMS = {
    "en": (
        # "dim the lamp 20 percent" can mean either, as can "fade". "dim it to 20"
        # is a level.
        r"(?:increase|decrease|raise|lower|reduce|boost|add|brighten|bump|drop|fade)",
        r"dim\b",
        r"(?:up|down|more|less|plus|minus|brighter|dimmer|darker)\b",
    ),
    "de": (
        # "dimme die Lampe 20 Prozent" can mean either, as "dim" can.
        "dimm",
        "erhöh",
        "verringer",
        "reduzier",
        "senk",
        "heller",
        "dunkler",
        "mehr\b",
        "weniger",
        "plus\b",
        "minus\b",
    ),
    "nl": (
        "verhoog",
        "verlaag",
        "feller",
        "lichter",
        "donkerder",
        "hoger",
        "lager",
        "omhoog",
        "omlaag",
        "meer\b",
        "minder\b",
        "min\b",
    ),
    "fr": ("augment", "baiss", "diminu", "rédui", "redui", "plus\b", "moins\b"),
    "it": ("aument", "abbass", "diminu", "riduc", "più\b", "piu\b", "meno\b"),
    "es": ("aument", "sube", "baja", "disminu", "reduc", "más\b", "menos\b"),
    "pt-BR": ("aument", "diminu", "reduz", "mais\b", "menos\b"),
    "pl": (
        "zwiększ",
        "zmniejsz",
        "podnieś",
        "obniż",
        "jaśniej",
        "ciemniej",
        "więcej",
        "mniej",
    ),
    "sv": ("dämp", "öka", "sänk", "minska", "ljusare", "mörkare", "mer\b", "mindre\b"),
    "da": ("øg\b", "sænk", "lysere", "mørkere", "mere\b", "mindre\b"),
    "cs": ("zvyš", "zvýš", "sniž", "jasněji", "tmavěji", "víc", "méně"),
    "ru": (
        "увелич",
        "уменьш",
        "прибав",
        "убав",
        "повыс",
        "пониз",
        "ярче",
        "темнее",
        "больше",
        "меньше",
    ),
    "hu": ("növel", "csökkent", "halványabb", "világosabb", "fényesebb", "sötétebb"),
}
_CHANGE = re.compile(
    r"\b(?:" + "|".join(s for g in _CHANGE_STEMS.values() for s in g) + ")",
    re.IGNORECASE,
)
# No spaces in Chinese, so a word boundary never fires in front of these.
_CHANGE_CJK = (
    "增加",
    "减少",
    "降低",
    "提高",
    "调亮",
    "调暗",
    "调高",
    "调低",
    "更亮",
    "更暗",
    # Japanese and Korean, which the integration is not translated into, for a
    # satellite that is: "ランプを20%明るく" set 20.
    "明るく",
    "暗く",
    "밝게",
    "어둡게",
)
# A comparative straight after the number is an amount even behind "to": the
# sentence says "20% brighter", not "to 20%".
_COMPARATIVE_AFTER = re.compile(
    r"^\s*(?:%|" + "|".join(_PERCENT_WORDS[1:]) + r")?\s*(?:"
    r"brighter|dimmer|darker|more|less|heller|dunkler|feller|lichter|donkerder"
    r"|plus|off\b|ljusare|mörkare|lysere|mørkere|ярче|темнее|更亮|更暗)",
    re.IGNORECASE,
)


# The same "to" words anywhere in the sentence, for a level said in words, where
# there is no number to stand in front of. A word taken for "to" only leaves the
# decision to the model, so these are read in the pipeline's language: with every
# language at once, Spanish "a" and the Hungarian suffix are in "turn up the lamp a
# bit" and "dim it some more", and the change words never refused an English
# sentence.
_TO_WORDS = {
    "en": r"\b(?:to|at)\b",
    "de": r"\b(?:auf|zu)\b",
    "nl": r"\b(?:op|naar|tot)\b",
    "fr": r"\b(?:à|a|au)\b",
    "it": r"\b(?:a|al|allo|alla)\b",
    "es": r"\b(?:a|al|para)\b",
    "pt": r"\b(?:a|para)\b",
    "pl": r"\b(?:na|do)\b",
    "sv": r"\b(?:på|till)\b",
    "da": r"\b(?:på|til)\b",
    "cs": r"\b(?:na|do)\b",
    "ru": r"\bдо\b",  # noqa: RUF001
    "hu": r"\w(?:ra|re)\b",
    "zh": "到|为|成|至",
}
_TO_IN = {
    language: re.compile(words, re.IGNORECASE) for language, words in _TO_WORDS.items()
}
_TO_ANYWHERE = re.compile("|".join(_TO_WORDS.values()), re.IGNORECASE)

# Zero said in words. A score's lowest level is 10%, so "zero percent" read as 10
# and turned the lamp on, three runs of three in English and in Dutch.
_ZERO = re.compile(
    r"\b(?:zero|zéro|nul|null|cero|nulla|noll|nula|ноль|нуль)\b|零", re.IGNORECASE
)

# A number on a scale of its own is not a percentage: "3 out of 10" set 10 and
# "level 5" set 5.
_SCALE_AFTER = re.compile(r"^\s*(?:/|out of\b)", re.IGNORECASE)
_SCALE_BEFORE = re.compile(
    r"(?:\b(?:level|stufe|niveau|stand|nivel|livello|poziom|nivå|úroveň|уровень|szint)"
    r"|/|\bout of)\s*$",
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
    "hu": ("fény", "halvány", "világos"),
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


def find_brightness(text: str, *, bare: bool = True) -> int | None:
    """The level a sentence sets, if it says one.

    Prefers an explicit percent sign, because "turn on 2 lamps" holds a number that
    is not a brightness. Without one, the last number wins, because a device name
    comes before its level: "lamp 2 brightness to 40" means 40. A number that is an
    amount to change the level by gives None. bare=False reads only a number with a
    percent sign or word.
    """
    found = (
        _PERCENT.search(text)
        or _PERCENT_PREFIX.search(text)
        or (_last_level_number(text) if bare else None)
    )
    if found is None or _is_an_amount(text, found) or _is_a_scale(text, found):
        return None
    return _in_range(found.group(1))


def without_names(text: str, names: Iterable[str]) -> str:
    """The text with every name that holds a digit taken out.

    "set lamp 2 brightness to fifty percent" set 2, because the 2 was the only
    digit. Taken out, the sentence has no digit, and the level is asked for.
    """
    held = {n for n in names if any(c.isdigit() for c in n)}
    for name in sorted(held, key=len, reverse=True):
        phrase = r"\s+".join(re.escape(w) for w in name.split())
        text = re.sub(rf"(?<!\w){phrase}(?!\w)", " ", text, flags=re.IGNORECASE)
    return text


def said_a_digit(text: str) -> bool:
    """Whether the regex had a number to read, so its answer is the last word."""
    return _BARE_NUMBER.search(text) is not None


# The score confidence a level said in words needs, above the agent's own floor. In
# four runs of 20 levels in words, every level set scored 0.92 or more. An earlier
# run read "тридцать процентов" as 40 at 0.67.
_LEVEL_FLOOR = 0.8

# The levels a level said in words is read against. A score takes ten at most, so
# a word gives a whole ten: "a quarter" scored 21.0 and sets 20.
_WORD_LEVELS = tuple(range(10, 101, 10))


def level_questions() -> dict[str, Question]:
    """The second request, for a brightness command with no digit in it."""
    return {
        "level": Score(
            "Which brightness level does the command set the light to?",
            [f"{n}%" for n in _WORD_LEVELS],
        ),
        # A score has no "none", so "dim the lamp by twenty percent" came back as
        # 78. This question is what refuses it.
        "relative": Noul(
            "Does the command change the brightness by an amount or in a "
            "direction, rather than name the level?",
            true="It says brighter, dimmer, up, down or by how much",
            false="It names the level",
        ),
    }


def read_level(
    response: JevResponse,
    text: str,
    min_confidence: float,
    language: str | None = None,
) -> int | None:
    """The level a sentence says in words, or None to leave it to the fallback.

    Three things refuse. On 22 amounts in words in 10 languages, run twice, only the
    change words refused "把灯调亮百分之二十": it scored 0.84 and 0.90 as a level and
    0.15 and 0.12 as an amount. The four amounts with no change word, such as "make
    the lamp a bit dimmer", were refused by both the amount question and the floor,
    at 0.69 or less. On 20 levels in words, four runs set 17 right each time and
    refused 3.

    A zero word gives 0, when the model put the sentence on the lowest level.
    """
    level = response.answers.get("level")
    relative = response.answers.get("relative")
    if not isinstance(level, ScoreAnswer) or not isinstance(relative, NoulAnswer):
        return None
    if relative.noul >= 0.5 or level.confidence < max(min_confidence, _LEVEL_FLOOR):
        return None
    if _names_a_change(text, language):
        return None
    index = min(round(level.score), len(_WORD_LEVELS) - 1)
    if _ZERO.search(text):
        return 0 if index == 0 else None
    return _WORD_LEVELS[index]


def _names_a_change(text: str, language: str | None) -> bool:
    change = _CHANGE.search(text) or any(word in text for word in _CHANGE_CJK)
    to = _TO_IN.get((language or "").split("-")[0].lower(), _TO_ANYWHERE)
    return bool(change) and not to.search(text)


def _last_level_number(text: str) -> re.Match[str] | None:
    if not (_LEVEL.search(text) or any(word in text for word in _LEVEL_CJK)):
        return None
    *_, last = (None, *_BARE_NUMBER.finditer(text))
    return last


def _is_a_scale(text: str, number: re.Match[str]) -> bool:
    before, after = text[: number.start()], text[number.end() :]
    return bool(_SCALE_AFTER.search(after) or _SCALE_BEFORE.search(before))


def _is_an_amount(text: str, number: re.Match[str]) -> bool:
    before, after = text[: number.start()], text[number.end() :]
    # "百分之" sits in front of the number, so the words before it come before that.
    before = before.removesuffix("百分之").rstrip()
    # A sign is an amount: "lamp +20%" set 20.
    if before.endswith(("+", "-", "−", "±")):  # noqa: RUF001
        return True
    if _COMPARATIVE_AFTER.search(after) or _BY_SUFFIX.search(after):
        return True
    if _TO.search(before) or _TO_SUFFIX.search(after):
        return False
    if _BY.search(before):
        return True
    return bool(_CHANGE.search(text)) or any(word in text for word in _CHANGE_CJK)


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
    # A brightness command whose level was said in words. The agent asks for it
    # before it acts.
    needs_level: bool = False

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
                # No playback wording either. "Stop the music" is not a power
                # command, and a player without turn_off fails it.
                "turn_on": "Switch something on, open it, or run a script or scene",
                "turn_off": "Switch something off or close it",
                "toggle": "Flip whatever state it is in now",
                "set_brightness": "Change how bright a light is",
                "get_state": "Answer a question about the current state, "
                "changing nothing",
                NONE: "None of these, such as playing, pausing, stopping or "
                "skipping media, or the request is not about the house",
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
        # Home Assistant's own agent has no timer or condition for an on/off
        # command, so "turn off the lamp in 10 minutes" would turn it off now.
        # A brightness level is not a part-way position, so each gets its own
        # question: one that asked about both scored "set the lamp to 40
        # percent" at 0.64 and refused it.
        "later": Noul(
            "Does the command say to do it at another time, for a set time, or "
            "only if something happens?",
            true="It gives a time, a delay, a duration or a condition",
            false="It is to be done now",
        ),
        # turn_on opens a cover all the way, so "open the blinds halfway" read as
        # turn_on opens them fully.
        "part": Noul(
            "Does the command ask to open or close something only part of the way?",
            true="It asks for a position between open and closed",
            false="It asks for fully open or closed, or it is not about opening "
            "or closing",
        ),
        "target_type": Choice(
            "How is the target named?",
            {
                "entity": "One particular device is named",
                "area": "A room or area is named, covering what is in it",
                "everything": "Every device, or every device of one kind such as "
                "all the lights, with no room or device named",
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
    if noul("later") >= 0.5:
        return out("for another time or on a condition")
    if noul("part") >= 0.5:
        return out("a position part of the way")

    # A digit in a name is not a level: "lamp 2", "Bedroom 2".
    spoken = without_names(
        text,
        [
            *(e.name for e in snapshot.entities),
            *snapshot.areas,
            *snapshot.floors,
            *snapshot.hidden_names,
        ],
    )
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
        # "turn on the lamp at 50%" with the lamp on is a new level, not done.
        # It came back as already on in 1 of 2 runs.
        if find_brightness(spoken, bare=False) is None and (
            settled := _already_done(action, entity, snapshot, min_confidence)
        ):
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
        # The model picks the closest option it was shown, and a hidden device is
        # not one of them. Measured on a development instance with an unexposed
        # "Desk lamp" and an exposed "Lamp": "turn on the desk lamp" came back as
        # Lamp and switched it on, three times of three.
        if _a_hidden_name_fits_better(text, described, snapshot):
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
        # "Turn on the desk lamp" with a hidden desk lamp would otherwise ask
        # which of two exposed lamps was meant.
        if _a_hidden_name_fits_better(text, unsure, snapshot):
            return out("named a device that is not exposed")
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

    needs_level = False
    # "turn on the lamp at 50%" scored turn_on, and HassTurnOn has no level, so the
    # lamp came on at whatever it was before. Only a percent counts here: a bare
    # number next to "ljus" or "свет", which are also the words for a light, would
    # read "tänd 2 ljus" as 2%.
    if (
        action.choice == "turn_on"
        and "light" in slots.get("domain", {}).get("value", [])
        and (level := find_brightness(spoken, bare=False)) is not None
    ):
        intent_type = ACTIONS["set_brightness"]
        slots["brightness"] = {"value": level}
        slots["domain"] = {"value": ["light"]}
    if action.choice == "set_brightness":
        brightness = find_brightness(spoken)
        if brightness is not None:
            slots["brightness"] = {"value": brightness}
        elif said_a_digit(spoken):
            return out("a brightness was asked for but no level was said")
        else:
            needs_level = True
        slots["domain"] = {"value": ["light"]}

    return Interpretation(
        intent_type=intent_type,
        slots=slots,
        action=action.choice,
        confidence=action.confidence,
        # The trace keeps this before the level is read, so "ok" would be early.
        reason="the level is said in words and asked for next" if needs_level else "ok",
        fallback=False,
        targets_everything=targets_everything,
        needs_level=needs_level,
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


def _a_hidden_name_fits_better(
    text: str, chosen: ExposedEntity, snapshot: HomeSnapshot
) -> bool:
    """A hidden name that the command says in more words than the chosen one."""
    said = _words_said(text, chosen.name)
    return any(_words_said(text, name) > said for name in snapshot.hidden_names)


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
