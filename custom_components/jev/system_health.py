"""System health: can this Home Assistant reach the API at all."""

from __future__ import annotations

from typing import Any

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback
from jevclient import DEFAULT_BASE_URL


@callback
def async_register(
    hass: HomeAssistant, register: system_health.SystemHealthRegistration
) -> None:
    register.async_register_info(system_health_info)


async def system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    return {
        "reachable": system_health.async_check_can_reach_url(hass, DEFAULT_BASE_URL),
    }
