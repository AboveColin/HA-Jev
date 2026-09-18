"""One coordinator per context, plus the usage accounting shared by an entry."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ServiceValidationError,
    TemplateError,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.target import (
    async_track_target_selector_state_change_event,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from jevclient import (
    USD_PER_MILLION_INPUT_TOKENS,
    Answer,
    JevAuthError,
    JevClient,
    JevError,
    JevRateLimitError,
)

from .const import (
    CONVERSATION_TRACE_LENGTH,
    DOMAIN,
    ISSUE_BUDGET_EXCEEDED,
    MIN_UPDATE_INTERVAL_SECONDS,
    STORE_SAVE_DELAY_SECONDS,
    TRIGGER_DEBOUNCE_SECONDS,
)
from .models import ContextConfig
from .statebuilder import async_build_state

_LOGGER = logging.getLogger(__name__)


@dataclass
class UsageAccount:
    """What this config entry has spent today.

    The token counts are what the API reported, not an estimate. The money is an
    estimate, because the price is a setting and TypeSafe can change theirs.

    The totals are persisted. Home Assistant restarts, and so does a reload after
    an options change, and a daily budget that either of those clears would not be
    a daily budget at all.
    """

    day: date
    calls: int = 0
    input_tokens: int = 0
    budget: int = 0
    price_per_million: float = USD_PER_MILLION_INPUT_TOKENS
    budget_exceeded: bool = False
    listeners: list[Any] = field(default_factory=list)
    store: Store[dict[str, Any]] | None = None

    def as_stored(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "calls": self.calls,
            "input_tokens": self.input_tokens,
        }

    def restore(self, stored: dict[str, Any] | None) -> None:
        """Adopt yesterday's file only if it is actually today's."""
        if not stored:
            return
        try:
            stored_day = date.fromisoformat(stored["day"])
        except (KeyError, TypeError, ValueError):
            return
        if stored_day != self.day:
            return
        self.calls = int(stored.get("calls", 0))
        self.input_tokens = int(stored.get("input_tokens", 0))

    def _save(self) -> None:
        if self.store is not None:
            self.store.async_delay_save(self.as_stored, STORE_SAVE_DELAY_SECONDS)

    def roll_over(self, today: date) -> None:
        if today != self.day:
            self.day = today
            self.calls = 0
            self.input_tokens = 0
            self.budget_exceeded = False
            self._save()

    def record(self, input_tokens: int) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self._save()

    @property
    def estimated_cost(self) -> float:
        return self.input_tokens / 1_000_000 * self.price_per_million

    def would_exceed(self) -> bool:
        return self.budget > 0 and self.input_tokens >= self.budget

    @callback
    def notify(self) -> None:
        for listener in list(self.listeners):
            listener()


@dataclass
class JevRuntimeData:
    """Everything a config entry owns while it is loaded."""

    client: JevClient
    usage: UsageAccount
    coordinators: dict[str, JevCoordinator] = field(default_factory=dict)
    model_version: str | None = None
    # What the conversation agent decided, most recent first. Bounded, because a
    # satellite that mishears a wake word all night must not grow this without end.
    conversation_traces: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=CONVERSATION_TRACE_LENGTH)
    )


class JevCoordinator(DataUpdateCoordinator[dict[str, Answer]]):
    """Evaluates one context: render the template, ask every question, store answers."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        runtime: JevRuntimeData,
        context: ContextConfig,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {context.name}",
            update_interval=timedelta(
                seconds=max(context.scan_interval, MIN_UPDATE_INTERVAL_SECONDS)
            ),
            config_entry=entry,
        )
        self.context_config = context
        self.runtime = runtime
        self.last_state_text: str | None = None
        self.last_latency_ms: float | None = None
        # Log once when it goes away and once when it comes back. A context that
        # evaluates every 30 s would otherwise write 2,880 identical lines a day
        # during an outage, which buries the one line that mattered.
        self._logged_unavailable = False
        self._unsub_triggers: Any = None
        self._debouncer: Debouncer[Coroutine[Any, Any, None]] | None = None

    async def async_setup_triggers(self) -> None:
        """Re-evaluate when what the context looks at changes, debounced.

        Without the debounce a power sensor updating every second would issue a paid
        request every second. A context that names entities tracks exactly those
        unless it says otherwise, because the thing it reads and the thing that
        should wake it are almost always the same list.
        """
        context = self.context_config
        debouncer = self._debouncer = Debouncer(
            self.hass,
            _LOGGER,
            cooldown=TRIGGER_DEBOUNCE_SECONDS,
            immediate=False,
            function=self.async_request_refresh,
        )

        @callback
        def _changed(_event: Any) -> None:
            self.hass.async_create_task(debouncer.async_call())

        if context.trigger_entities:
            self._unsub_triggers = async_track_state_change_event(
                self.hass, context.trigger_entities, _changed
            )
        elif context.selector:
            # Tracking the selector rather than a fixed list means an entity added
            # to a targeted area later starts waking the context on its own.
            # This one is an async function, whatever its -> CALLBACK_TYPE
            # annotation says. Assigning it without awaiting stores a coroutine, the
            # tracker never registers, and unload later fails on calling it.
            self._unsub_triggers = await async_track_target_selector_state_change_event(
                self.hass, context.selector, _changed
            )

    @callback
    def async_shutdown_triggers(self) -> None:
        if self._unsub_triggers is not None:
            self._unsub_triggers()
            self._unsub_triggers = None
        if self._debouncer is not None:
            # A debounce scheduled just before unload would otherwise fire into a
            # coordinator that no longer has a config entry behind it.
            self._debouncer.async_shutdown()
            self._debouncer = None

    async def _async_update_data(self) -> dict[str, Answer]:
        usage = self.runtime.usage
        usage.roll_over(date.today())

        if usage.would_exceed():
            self._raise_budget_issue(usage)
            # Keep the answers already held. Inventing a value here would be worse
            # than staying still, and clearing them would hide the last real result.
            raise UpdateFailed(
                f"daily token budget reached: budget {usage.budget} input tokens, "
                f"used {usage.input_tokens} today. Raise or clear the budget in the "
                f"integration options to continue."
            )

        context = self.context_config
        try:
            text = (
                context.template.async_render(parse_result=False)
                if context.template is not None
                else None
            )
        except TemplateError as err:
            raise UpdateFailed(
                f"the state template for context {context.name!r} failed: {err}"
            ) from err
        try:
            state_text = async_build_state(
                self.hass, text, context.selector, context.include_attributes
            )
        except ServiceValidationError as err:
            # A picked device or area that has since been removed. Saying so beats
            # quietly asking about whatever is left.
            raise UpdateFailed(f"context {context.name!r}: {err}") from err

        questions = {q.key: q.question for q in self.context_config.questions}
        try:
            response = await self.runtime.client.ask(state_text, questions)
        except JevAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except JevRateLimitError as err:
            raise UpdateFailed(f"rate limited by TypeSafe: {err}") from err
        except JevError as err:
            self._log_unavailable_once(err)
            raise UpdateFailed(str(err)) from err

        if self._logged_unavailable:
            _LOGGER.info("TypeSafe is answering again, context %r resumed", context.name)
            self._logged_unavailable = False
        usage.record(response.usage.input_tokens)
        self.runtime.model_version = response.model or self.runtime.model_version
        self.last_state_text = state_text
        self.last_latency_ms = response.latency_ms
        usage.notify()
        return response.answers

    def _log_unavailable_once(self, err: Exception) -> None:
        if self._logged_unavailable:
            return
        self._logged_unavailable = True
        _LOGGER.error(
            "TypeSafe is not answering, so context %r cannot be evaluated: %s",
            self.context_config.name,
            err,
        )

    def _raise_budget_issue(self, usage: UsageAccount) -> None:
        if usage.budget_exceeded:
            return
        usage.budget_exceeded = True
        _LOGGER.error(
            "Jev stopped evaluating: daily budget is %s input tokens, %s used today, "
            "and context %r needs another call. Raise the budget in the integration "
            "options or reduce how often contexts evaluate.",
            usage.budget,
            usage.input_tokens,
            self.context_config.name,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            ISSUE_BUDGET_EXCEEDED,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_BUDGET_EXCEEDED,
            translation_placeholders={
                "budget": str(usage.budget),
                "used": str(usage.input_tokens),
            },
        )
        usage.notify()
