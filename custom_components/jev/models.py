"""The shapes the integration passes around, built from YAML or from a service call."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.helpers.template import Template
from jevclient import Choice, Noul, Question, Score

from .const import (
    CONF_CRITERIA,
    CONF_FALSE,
    CONF_INSTRUCTIONS,
    CONF_THRESHOLD,
    CONF_TRUE,
    TYPE_CHOICE,
    TYPE_NOUL,
    TYPE_SCORE,
)


@dataclass(slots=True)
class QuestionConfig:
    """One configured question, and the entity settings that come with it."""

    key: str
    name: str
    kind: str
    question: Question
    threshold: float | None = None

    @property
    def wants_binary_sensor(self) -> bool:
        return self.kind == TYPE_NOUL and self.threshold is not None

    @property
    def options(self) -> list[str]:
        """The allowed states of a choice, for the enum device class."""
        if isinstance(self.question, Choice):
            return list(self.question.criteria)
        return []


@dataclass(slots=True)
class ContextConfig:
    """What to look at, and every question asked about it.

    One context is one API call. The API evaluates questions in isolation and in
    parallel against the same state, so adding a question to an existing context
    costs a few hundred tokens and almost no extra time, while adding a second
    context costs a whole extra call.
    """

    key: str
    name: str
    questions: list[QuestionConfig]
    scan_interval: int
    template: Template | None = None
    selector: dict[str, Any] | None = None
    include_attributes: bool = False
    trigger_entities: list[str] = field(default_factory=list)


def build_question(raw: dict[str, Any]) -> Question:
    """Turn one YAML question block into a library question object."""
    kind = raw["type"]
    instructions = raw[CONF_INSTRUCTIONS]
    if kind == TYPE_NOUL:
        return Noul(
            instructions,
            true=raw.get(CONF_TRUE),
            false=raw.get(CONF_FALSE),
        )
    if kind == TYPE_CHOICE:
        return Choice(instructions, raw[CONF_CRITERIA])
    if kind == TYPE_SCORE:
        return Score(instructions, raw[CONF_CRITERIA])
    raise ValueError(f"unknown question type {kind!r}")


def build_question_config(raw: dict[str, Any], key: str) -> QuestionConfig:
    return QuestionConfig(
        key=key,
        name=raw["name"],
        kind=raw["type"],
        question=build_question(raw),
        threshold=raw.get(CONF_THRESHOLD),
    )
