"""The fix flow behind each house check card: ignore, snooze or turn off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .house_check import HouseCheck


class HouseCheckFlow(RepairsFlow):
    """One card's choices. Turn off shows only where it can act."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id
        # Set when the menu offers to turn it off. Empty, a turn-off is refused.
        self._target = ""

    def _check(self) -> HouseCheck | None:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            return None
        check: HouseCheck = entry.runtime_data.house_check
        return check

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        check = self._check()
        if check is None:
            return self.async_abort(reason="not_loaded")
        options = ["ignore", "snooze"]
        if target := check.turn_off_target(self.issue_id):
            self._target = target
            options.append("turn_off")
        # The card's own words live in this step, so it needs the card's values.
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
        return self.async_show_menu(
            step_id="init",
            menu_options=options,
            description_placeholders=issue.translation_placeholders if issue else None,
        )

    async def async_step_ignore(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        if (check := self._check()) is None:
            return self.async_abort(reason="not_loaded")
        await check.async_ignore(self.issue_id)
        return self.async_create_entry(data={})

    async def async_step_snooze(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        if (check := self._check()) is None:
            return self.async_abort(reason="not_loaded")
        await check.async_snooze(self.issue_id)
        return self.async_create_entry(data={})

    async def async_step_turn_off(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        if (check := self._check()) is None:
            return self.async_abort(reason="not_loaded")
        await check.async_turn_off(self._target, None)
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Every fixable card this integration raises is a house check card."""
    return HouseCheckFlow(str((data or {}).get("entry_id", "")))
