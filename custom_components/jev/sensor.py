"""Sensors: one per question, plus the usage receipts for the config entry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from jevclient import ChoiceAnswer, NoulAnswer, ScoreAnswer

from .const import (
    ATTR_CONFIDENCE,
    ATTR_LEGEND,
    ATTR_NEAREST_LEVEL,
    ATTR_PROBABILITIES,
    TYPE_CHOICE,
    TYPE_NOUL,
    TYPE_SCORE,
)
from .coordinator import JevCoordinator, JevRuntimeData
from .entity import JevQuestionEntity, JevUsageEntity
from .models import QuestionConfig

if TYPE_CHECKING:
    from . import JevConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JevConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entities: list[Any] = []
    for coordinator in runtime.coordinators.values():
        for question in coordinator.context_config.questions:
            entities.append(JevQuestionSensor(coordinator, entry.entry_id, question))
        entities.append(JevLatencySensor(coordinator, entry.entry_id))
    entities.extend(
        [
            JevCallsSensor(entry.entry_id, runtime),
            JevInputTokensSensor(entry.entry_id, runtime),
            JevCostSensor(entry.entry_id, runtime),
        ]
    )
    async_add_entities(entities)


class JevQuestionSensor(JevQuestionEntity, SensorEntity):
    """The answer to one question.

    A noul reads as the probability of yes, a score as a probability-weighted level
    which can land between two levels, and a choice as the winning option.
    """

    def __init__(
        self, coordinator: JevCoordinator, entry_id: str, question: QuestionConfig
    ) -> None:
        super().__init__(coordinator, entry_id, question.key)
        self._question = question
        self._attr_name = question.name
        self._attr_unique_id = f"{entry_id}_{question.key}"
        if question.kind == TYPE_CHOICE:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = question.options
        else:
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | str | None:
        answer = (self.coordinator.data or {}).get(self._question_key)
        if answer is None:
            return None
        if isinstance(answer, NoulAnswer):
            return round(answer.noul, 3)
        if isinstance(answer, ChoiceAnswer):
            return answer.choice
        if isinstance(answer, ScoreAnswer):
            return round(answer.score, 3)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        answer = (self.coordinator.data or {}).get(self._question_key)
        if answer is None:
            return None
        if isinstance(answer, NoulAnswer):
            # A noul carries no confidence: the probability is the whole answer.
            return {"question_type": TYPE_NOUL}
        if isinstance(answer, ChoiceAnswer):
            return {
                "question_type": TYPE_CHOICE,
                ATTR_CONFIDENCE: round(answer.confidence, 3),
                ATTR_PROBABILITIES: {
                    k: round(v, 4) for k, v in answer.probabilities.items()
                },
            }
        if isinstance(answer, ScoreAnswer):
            return {
                "question_type": TYPE_SCORE,
                ATTR_CONFIDENCE: round(answer.confidence, 3),
                ATTR_PROBABILITIES: {
                    k: round(v, 4) for k, v in answer.probabilities.items()
                },
                ATTR_LEGEND: answer.legend,
                ATTR_NEAREST_LEVEL: answer.nearest_level,
            }
        return None


class JevLatencySensor(JevQuestionEntity, SensorEntity):
    """How long the last evaluation of this context took."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: JevCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, question_key="")
        self._attr_name = f"{coordinator.context_config.name} latency"
        self._attr_unique_id = f"{entry_id}_{coordinator.context_config.key}_latency"

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> float | None:
        if self.coordinator.last_latency_ms is None:
            return None
        return round(self.coordinator.last_latency_ms)


class JevCallsSensor(JevUsageEntity, SensorEntity):
    """Calls made today. Resets at midnight, local time."""

    _attr_name = "Calls today"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, entry_id: str, runtime: JevRuntimeData) -> None:
        super().__init__(entry_id, runtime)
        self._attr_unique_id = f"{entry_id}_calls_today"

    @property
    def native_value(self) -> int:
        return self._runtime.usage.calls


class JevInputTokensSensor(JevUsageEntity, SensorEntity):
    """Input tokens today, as reported by the API rather than estimated.

    Question text is billed as input, so this rises with the number of questions
    per context, not only with the size of the state.
    """

    _attr_name = "Input tokens today"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "tokens"

    def __init__(self, entry_id: str, runtime: JevRuntimeData) -> None:
        super().__init__(entry_id, runtime)
        self._attr_unique_id = f"{entry_id}_input_tokens_today"

    @property
    def native_value(self) -> int:
        return self._runtime.usage.input_tokens

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        usage = self._runtime.usage
        return {"daily_token_budget": usage.budget or None}


class JevCostSensor(JevUsageEntity, SensorEntity):
    """Estimated spend today.

    The token count is measured; the money is an estimate, because the price per
    million is a setting and TypeSafe can change theirs.
    """

    _attr_name = "Estimated cost today"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "USD"
    _attr_suggested_display_precision = 4

    def __init__(self, entry_id: str, runtime: JevRuntimeData) -> None:
        super().__init__(entry_id, runtime)
        self._attr_unique_id = f"{entry_id}_estimated_cost_today"

    @property
    def native_value(self) -> float:
        return round(self._runtime.usage.estimated_cost, 6)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"price_per_million_input_tokens": self._runtime.usage.price_per_million}
