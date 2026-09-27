"""What a blueprint case is, and the helpers that fire a trigger.

The user's actions call `test.yes`, `test.no` and `test.other`, and put the
variables the blueprint promises into their data. A case passes only when the right
one ran with the right values, so a variable the description names but the
blueprint never sets fails here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

NO_WAIT = {"seconds": 0}


def run(service: str, **data: str) -> list[dict[str, Any]]:
    return [{"action": f"test.{service}", "data": data}]


def change(entity_id: str, state: str, **attributes: Any) -> Callable:
    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        hass.states.async_set(entity_id, state, attributes)
        await hass.async_block_till_done()
        # Past any hold time a case sets, so a `for:` trigger fires.
        freezer.tick(timedelta(minutes=61))
        async_fire_time_changed(hass, dt_util.utcnow())

    return fire


def at(hour: int, minute: int = 0) -> Callable:
    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        now = dt_util.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        freezer.move_to(target)
        async_fire_time_changed(hass, target)

    return fire


def event(event_type: str, **data: Any) -> Callable:
    async def fire(hass: HomeAssistant, freezer: Any) -> None:
        hass.bus.async_fire(event_type, data)

    return fire


@dataclass
class Case:
    inputs: dict[str, Any]
    fire: Callable[[HomeAssistant, Any], Awaitable[None]]
    answers: dict[str, Any]
    expect: dict[str, list[dict[str, Any]]]
    states: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)
    asks: int = 1
    check_request: Callable[..., None] | None = None
