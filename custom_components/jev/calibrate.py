"""Pick a threshold from what actually happened, instead of guessing one.

A noul is a probability, and the threshold that turns it into yes or no is the
user's call. 0.5 is where a noul says "cannot tell", not where a given house's
washing machine is done. This action reads the recorder: the noul's history next to
the history of an entity that says what was really true, such as a door contact or
a smart plug's own "running" state. For every threshold it measures how much of the
time a yes was right (precision) and how much of the true time it said yes
(recall), and returns the threshold that balances the two best.

Nothing is sent to TypeSafe, so it costs no tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Final

import voluptuous as vol
from homeassistant.components.recorder import history
from homeassistant.const import ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.recorder import get_instance
from homeassistant.util import dt as dt_util

from .const import DOMAIN

CONF_TRUTH_ENTITY: Final = "truth_entity_id"
CONF_TRUTH_STATE: Final = "truth_state"
CONF_DAYS: Final = "days"

# The recorder keeps 10 days by default (`purge_keep_days`), so a week is inside
# what a default install still has. A longer window only reads further back.
DEFAULT_DAYS: Final = 7

# Every hundredth. The noul sensor itself rounds to three places, but nobody sets a
# threshold that fine, and two neighbouring hundredths rarely differ in outcome.
CANDIDATES: Final = tuple(round(i / 100, 2) for i in range(1, 100))

# The coarse table returned next to the best threshold, so a caller can see how
# steep the trade-off is around it.
TABLE: Final = tuple(round(i / 10, 1) for i in range(1, 10))

CALIBRATE_SCHEMA: Final = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(CONF_TRUTH_ENTITY): cv.entity_id,
        vol.Optional(CONF_TRUTH_STATE, default=STATE_ON): cv.string,
        vol.Optional(CONF_DAYS, default=DEFAULT_DAYS): vol.All(
            vol.Coerce(int), vol.Range(min=1)
        ),
    }
)


@dataclass(frozen=True, slots=True)
class Span:
    """A stretch of time where neither the probability nor the truth changed."""

    probability: float
    truth: bool
    seconds: float


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one threshold would have said over the spans, in seconds."""

    threshold: float
    true_yes: float
    false_yes: float
    missed: float

    @property
    def precision(self) -> float | None:
        said_yes = self.true_yes + self.false_yes
        return self.true_yes / said_yes if said_yes else None

    @property
    def recall(self) -> float | None:
        was_true = self.true_yes + self.missed
        return self.true_yes / was_true if was_true else None

    @property
    def f1(self) -> float:
        precision, recall = self.precision, self.recall
        if not precision or not recall:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    def as_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "precision": _round(self.precision),
            "recall": _round(self.recall),
            "f1": round(self.f1, 3),
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def outcome(spans: list[Span], threshold: float) -> Outcome:
    true_yes = false_yes = missed = 0.0
    for span in spans:
        said_yes = span.probability >= threshold
        if said_yes and span.truth:
            true_yes += span.seconds
        elif said_yes:
            false_yes += span.seconds
        elif span.truth:
            missed += span.seconds
    return Outcome(threshold, true_yes, false_yes, missed)


def best(spans: list[Span]) -> Outcome:
    """The threshold with the highest F1.

    Several neighbouring thresholds often tie, because no probability fell between
    them. The middle of the tied ones is returned rather than an edge, so a noul
    that lands a little off its usual values still falls on the same side.
    """
    outcomes = [outcome(spans, threshold) for threshold in CANDIDATES]
    top = max(o.f1 for o in outcomes)
    tied = [o.threshold for o in outcomes if o.f1 == top]
    return outcome(spans, round(median(tied), 2))


def build_spans(
    probabilities: list[State],
    truths: list[State],
    truth_state: str,
    start: datetime,
    end: datetime,
) -> list[Span]:
    """Cut the window at every change of either entity.

    The state an entity already had when the window opened counts from the start of
    the window. A span where the probability is not a number, such as unavailable
    while the API was down, is left out rather than guessed.
    """
    changes = sorted(
        [(max(s.last_changed, start), "p", s.state) for s in probabilities]
        + [(max(s.last_changed, start), "t", s.state) for s in truths],
        key=lambda change: change[0],
    )
    spans: list[Span] = []
    probability: float | None = None
    truth: bool | None = None
    for index, (when, kind, value) in enumerate(changes):
        if kind == "p":
            probability = _number(value)
        else:
            truth = value == truth_state
        until = changes[index + 1][0] if index + 1 < len(changes) else end
        seconds = (until - when).total_seconds()
        if probability is not None and truth is not None and seconds > 0:
            spans.append(Span(probability, truth, seconds))
    return spans


def _number(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


async def async_calibrate(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    source = call.data[ATTR_ENTITY_ID]
    truth_entity = call.data[CONF_TRUTH_ENTITY]
    truth_state = call.data[CONF_TRUTH_STATE]
    days = call.data[CONF_DAYS]
    if "recorder" not in hass.config.components:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="calibrate_needs_recorder"
        )
    end = dt_util.utcnow()
    start = end - timedelta(days=days)
    found = await get_instance(hass).async_add_executor_job(
        _history, hass, start, end, [source, truth_entity]
    )
    spans = build_spans(
        found.get(source, []), found.get(truth_entity, []), truth_state, start, end
    )
    if not spans:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="calibrate_no_history",
            translation_placeholders={
                "entity": source,
                "truth": truth_entity,
                "days": str(days),
            },
        )
    true_seconds = sum(span.seconds for span in spans if span.truth)
    # With only one side there is nothing to separate, and every threshold scores
    # the same.
    if true_seconds in (0, sum(span.seconds for span in spans)):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=(
                "calibrate_never_true" if true_seconds == 0 else "calibrate_always_true"
            ),
            translation_placeholders={
                "truth": truth_entity,
                "state": truth_state,
                "days": str(days),
            },
        )
    return {
        **best(spans).as_dict(),
        "hours": round(sum(span.seconds for span in spans) / 3600, 1),
        "hours_true": round(true_seconds / 3600, 1),
        # F1 over two occasions is noise, whatever its value. These are the counts
        # to judge it by.
        "times_true": _times_true(spans),
        "probability_changes": sum(
            1 for s in found.get(source, []) if _number(s.state) is not None
        ),
        "table": [outcome(spans, threshold).as_dict() for threshold in TABLE],
    }


def _times_true(spans: list[Span]) -> int:
    """How many separate stretches the truth was on for."""
    count = 0
    previous = False
    for span in spans:
        if span.truth and not previous:
            count += 1
        previous = span.truth
    return count


def _history(
    hass: HomeAssistant, start: datetime, end: datetime, entity_ids: list[str]
) -> dict[str, list[State]]:
    found = history.get_significant_states(
        hass,
        start,
        end,
        entity_ids,
        include_start_time_state=True,
        significant_changes_only=False,
        no_attributes=True,
    )
    return {
        entity_id: [s for s in states if isinstance(s, State)]
        for entity_id, states in found.items()
    }
