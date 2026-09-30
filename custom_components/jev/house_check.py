"""A look at the house for things that are probably wrong, shown as Repairs cards.

Two checks read Home Assistant only and cost no tokens: an entity that has been
unavailable for a week, and a battery below 10%. The third asks Jev one yes/no
question, all in one request, for each entity the conversation agent can control:
is this state a mistake someone would want to fix now, given the time and the rest
of the house. That list comes from the snapshot, so it never holds a lock.

Each finding is a Repairs card. The unavailable entities of one config entry share
one card, because an integration that lost its device or its server takes every
entity with it: on one test instance, 438 of 518 came from one integration. A card's
fix flow ignores its entities, snoozes them for 30 days or, for a light, switch or
fan that is on, turns it off. A turn-off keeps the state it replaced, and
`jev.undo_house_check` puts it back. Nothing here changes a lock, cover, climate or
valve.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
    PERCENTAGE,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.state import async_reproduce_state
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from jevclient import Noul, NoulAnswer

from .calibrate import recorded_states
from .const import (
    DOMAIN,
    HOUSE_CHECK_EVERY_DAYS,
    ISSUE_HOUSE_CHECK_FAILED,
    ISSUE_LOOKS_WRONG,
    ISSUE_LOW_BATTERY,
    ISSUE_UNAVAILABLE,
    ISSUE_UNAVAILABLE_MANY,
    LOOKS_WRONG_MIN_NOUL,
    LOW_BATTERY_PERCENT,
    MAX_CONVERSATION_ENTITIES,
    SNOOZE_DAYS,
    STORAGE_VERSION,
    TURN_OFF_DOMAINS,
    UNAVAILABLE_DAYS,
    UNAVAILABLE_EXAMPLES,
)
from .services import async_ask
from .snapshot import async_snapshot

if TYPE_CHECKING:
    from . import JevConfigEntry

_LOGGER = logging.getLogger(__name__)

# The kinds that cost nothing, and so run on every check.
FREE_KINDS = (ISSUE_UNAVAILABLE, ISSUE_LOW_BATTERY)

# A scene's state is the time it last ran and a script's is whether it runs now, so
# neither can be left in a wrong state. They stay in the description as context.
NOT_ASKED_DOMAINS = ("scene", "script")

QUESTION = (
    "Look at entity {key} ({name}). Is its state a mistake that someone in the "
    "house would want to fix now, given the time and the rest of the house?"
)
TRUE_MEANS = "It was left like this by accident, or something is wrong with it"
FALSE_MEANS = "It is normal, or someone meant it, at this time"


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing the check thinks is wrong, and the card that says so."""

    kind: str
    # What the card's issue id ends in: the entity, or for a group of unavailable
    # entities, their config entry.
    key: str
    name: str
    state: str
    # What the card shows next to the name: the days unavailable, the battery
    # reading, or the yes/no value Jev gave.
    value: str
    entity_ids: tuple[str, ...]
    # The names of the first few entities, for a card that covers more than one.
    examples: str = ""

    @property
    def translation_key(self) -> str:
        if self.kind == ISSUE_UNAVAILABLE and len(self.entity_ids) > 1:
            return ISSUE_UNAVAILABLE_MANY
        return self.kind

    @property
    def placeholders(self) -> dict[str, str]:
        return {
            "name": self.name,
            "entity_id": self.entity_ids[0],
            "state": self.state,
            "value": self.value,
            "count": str(len(self.entity_ids)),
            "examples": self.examples,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.removeprefix("house_check_"),
            "name": self.name,
            "entity_ids": list(self.entity_ids),
            "state": self.state,
            "value": self.value,
        }


class HouseCheck:
    """One entry's findings, and what the user said about each of them."""

    def __init__(self, hass: HomeAssistant, entry: JevConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.house_check"
        )
        self.ignored: set[str] = set()
        self.snoozed: dict[str, datetime] = {}
        # The state a card's turn-off replaced, by entity, until it is undone.
        self.undo: dict[str, dict[str, Any]] = {}
        # The cards this entry has open, by issue id: the kind of each and the
        # entities it covers. A new run closes the ones of the kinds it checked and
        # did not find again.
        self.open: dict[str, dict[str, Any]] = {}
        self.last_run: datetime | None = None

    @property
    def failed_issue_id(self) -> str:
        return f"{ISSUE_HOUSE_CHECK_FAILED}_{self.entry.entry_id}"

    def issue_id(self, kind: str, key: str) -> str:
        return f"{kind}_{self.entry.entry_id}_{key}"

    def covered(self, issue_id: str) -> list[str]:
        """The entities an open card is about."""
        card = self.open.get(issue_id)
        return list(card["entity_ids"]) if card else []

    def turn_off_target(self, issue_id: str) -> str | None:
        """The one entity this card may turn off, if it may turn one off."""
        covered = self.covered(issue_id)
        if len(covered) == 1 and self.can_turn_off(covered[0]):
            return covered[0]
        return None

    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        self.ignored = set(stored.get("ignored", []))
        self.snoozed = {
            entity_id: until
            for entity_id, text in stored.get("snoozed", {}).items()
            if (until := dt_util.parse_datetime(text)) is not None
        }
        self.undo = stored.get("undo", {})
        self.open = stored.get("open", {})
        last_run = stored.get("last_run")
        self.last_run = dt_util.parse_datetime(last_run) if last_run else None

    async def async_remove(self) -> None:
        """Take down the entry's cards and what it stored, as the entry goes."""
        await self.async_load()
        for issue_id in [*self.open, self.failed_issue_id]:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        await self._store.async_remove()

    async def _async_save(self) -> None:
        # Written at once, not delayed: an undo record lost to a restart would leave
        # a light off with no way back.
        await self._store.async_save(
            {
                "ignored": sorted(self.ignored),
                "snoozed": {k: v.isoformat() for k, v in self.snoozed.items()},
                "undo": self.undo,
                "open": self.open,
                "last_run": self.last_run.isoformat() if self.last_run else None,
            }
        )

    def is_quiet(self, entity_id: str, now: datetime) -> bool:
        """Ignored for good, or snoozed until a time still ahead."""
        if entity_id in self.ignored:
            return True
        until = self.snoozed.get(entity_id)
        return until is not None and until > now

    async def async_run(self, use_jev: bool) -> list[Finding]:
        """Run the checks, open a card for each finding and close the rest.

        The free checks publish their cards even when the Jev check then fails,
        and that failure is raised after, so a spent budget is never silent.
        """
        now = dt_util.utcnow()
        self.snoozed = {k: v for k, v in self.snoozed.items() if v > now}
        findings = await self._free_findings(now)
        checked = set(FREE_KINDS)
        try:
            if use_jev:
                findings += await self._jev_findings(now)
                checked.add(ISSUE_LOOKS_WRONG)
                self.last_run = now
                ir.async_delete_issue(self.hass, DOMAIN, self.failed_issue_id)
        finally:
            self._publish(findings, checked)
            await self._async_save()
        return findings

    async def async_weekly(self, now: datetime) -> None:
        """Called each morning. Runs when the last full run is a week old."""
        if self.last_run is not None:
            days = (
                dt_util.as_local(now).date() - dt_util.as_local(self.last_run).date()
            ).days
            if days < HOUSE_CHECK_EVERY_DAYS:
                return
        try:
            await self.async_run(use_jev=True)
        except HomeAssistantError as err:
            _LOGGER.warning("The weekly house check did not reach Jev: %s", err)
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self.failed_issue_id,
                is_fixable=False,
                is_persistent=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_HOUSE_CHECK_FAILED,
                translation_placeholders={"reason": str(err)},
            )

    async def _free_findings(self, now: datetime) -> list[Finding]:
        unavailable = [
            s
            for s in self.hass.states.async_all()
            if s.state == STATE_UNAVAILABLE and not self.is_quiet(s.entity_id, now)
        ]
        found = self._grouped(await self._unavailable_all_week(now, unavailable))
        for state in self.hass.states.async_all(("sensor", "binary_sensor")):
            if state.attributes.get(ATTR_DEVICE_CLASS) != "battery" or self.is_quiet(
                state.entity_id, now
            ):
                continue
            if state.domain == "binary_sensor":
                # A battery binary sensor is on when the battery is low.
                if state.state == STATE_ON:
                    found.append(
                        Finding(
                            ISSUE_LOW_BATTERY,
                            state.entity_id,
                            state.name,
                            state.state,
                            state.state,
                            (state.entity_id,),
                        )
                    )
                continue
            if state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) != PERCENTAGE:
                continue
            try:
                level = float(state.state)
            except ValueError:
                continue
            if level < LOW_BATTERY_PERCENT:
                found.append(
                    Finding(
                        ISSUE_LOW_BATTERY,
                        state.entity_id,
                        state.name,
                        state.state,
                        f"{level:g} %",
                        (state.entity_id,),
                    )
                )
        return found

    def _grouped(self, states: list[State]) -> list[Finding]:
        """One finding for each config entry, and one for each entity without one."""
        registry = er.async_get(self.hass)
        groups: dict[str, list[State]] = {}
        for state in sorted(states, key=lambda s: s.entity_id):
            entry = registry.async_get(state.entity_id)
            key = entry.config_entry_id if entry and entry.config_entry_id else None
            groups.setdefault(key or state.entity_id, []).append(state)
        found: list[Finding] = []
        for key, members in groups.items():
            # A card of one names the entity, a card of many names the integration.
            config_entry = self.hass.config_entries.async_get_entry(key)
            found.append(
                Finding(
                    ISSUE_UNAVAILABLE,
                    key,
                    config_entry.title
                    if config_entry and len(members) > 1
                    else members[0].name,
                    STATE_UNAVAILABLE,
                    str(UNAVAILABLE_DAYS),
                    tuple(s.entity_id for s in members),
                    ", ".join(s.name for s in members[:UNAVAILABLE_EXAMPLES]),
                )
            )
        return found

    async def _unavailable_all_week(
        self, now: datetime, states: list[State]
    ) -> list[State]:
        """The entities that were unavailable for the whole of the last week.

        last_changed alone starts again at each restart, so an entity that has been
        gone for a month reads as gone for an hour after one. The recorder has the
        history across restarts. Without it, last_changed is the only evidence.
        """
        start = now - timedelta(days=UNAVAILABLE_DAYS)
        gone = {s.entity_id for s in states if s.last_changed <= start}
        rest = [s.entity_id for s in states if s.entity_id not in gone]
        if rest and "recorder" in self.hass.config.components:
            history = await get_instance(self.hass).async_add_executor_job(
                recorded_states, self.hass, start, now, rest
            )
            for entity_id, recorded in history.items():
                # The first state is the one in force at the start of the week. One
                # that begins later means the recorder does not reach back that far.
                if (
                    recorded
                    and recorded[0].last_changed <= start
                    and all(s.state == STATE_UNAVAILABLE for s in recorded)
                ):
                    gone.add(entity_id)
        return [s for s in states if s.entity_id in gone]

    async def _jev_findings(self, now: datetime) -> list[Finding]:
        """One request: the snapshot as the state, one noul for each entity in it."""
        snapshot = async_snapshot(self.hass, MAX_CONVERSATION_ENTITIES)
        described: list[dict[str, Any]] = []
        asked: dict[str, tuple[str, str, str]] = {}
        for index, entity in enumerate(snapshot.entities):
            key = f"e{index}"
            current = self.hass.states.get(entity.entity_id)
            since = current.last_changed if current else now
            described.append(
                {
                    "id": key,
                    "name": entity.name,
                    "kind": entity.domain,
                    "area": entity.area,
                    "state": entity.state,
                    # Starts again at a restart, like last_changed itself.
                    "unchanged_for_minutes": int((now - since).total_seconds() // 60),
                }
            )
            if (
                entity.domain not in NOT_ASKED_DOMAINS
                and entity.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
                and not self.is_quiet(entity.entity_id, now)
            ):
                asked[key] = (entity.entity_id, entity.name, entity.state)
        if not asked:
            return []
        state = {
            "now": dt_util.as_local(now).strftime("%A %H:%M"),
            "entities": described,
        }
        questions: dict[str, Any] = {
            key: Noul(
                QUESTION.format(key=key, name=name), true=TRUE_MEANS, false=FALSE_MEANS
            )
            for key, (_, name, _) in asked.items()
        }
        response = await async_ask(self.hass, self.entry, state, questions)
        found: list[Finding] = []
        for key, (entity_id, name, entity_state) in asked.items():
            answer = response.answers.get(key)
            if isinstance(answer, NoulAnswer) and answer.noul >= LOOKS_WRONG_MIN_NOUL:
                found.append(
                    Finding(
                        ISSUE_LOOKS_WRONG,
                        entity_id,
                        name,
                        entity_state,
                        f"{answer.noul:.2f}",
                        (entity_id,),
                    )
                )
        return found

    def _publish(self, findings: list[Finding], checked: set[str]) -> None:
        wanted = {self.issue_id(f.kind, f.key): f for f in findings}
        for issue_id, card in list(self.open.items()):
            if card["kind"] in checked and issue_id not in wanted:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                del self.open[issue_id]
        for issue_id, finding in wanted.items():
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=True,
                # A card that is not persistent is gone after a restart, and the
                # weekly run brings it back only a week later.
                is_persistent=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key=finding.translation_key,
                translation_placeholders=finding.placeholders,
                data={"entry_id": self.entry.entry_id},
            )
            self.open[issue_id] = {
                "kind": finding.kind,
                "entity_ids": list(finding.entity_ids),
            }

    def _close_cards(self, closes: Callable[[list[str]], bool]) -> None:
        for issue_id, card in list(self.open.items()):
            if closes(card["entity_ids"]):
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                del self.open[issue_id]

    def _close_quiet_cards(self) -> None:
        now = dt_util.utcnow()
        self._close_cards(lambda ids: all(self.is_quiet(e, now) for e in ids))

    async def async_ignore(self, issue_id: str) -> None:
        """Stop reporting what this card covers, on this card and any other."""
        self.ignored.update(self.covered(issue_id))
        self._close_quiet_cards()
        await self._async_save()

    async def async_snooze(self, issue_id: str) -> None:
        until = dt_util.utcnow() + timedelta(days=SNOOZE_DAYS)
        self.snoozed.update(dict.fromkeys(self.covered(issue_id), until))
        self._close_quiet_cards()
        await self._async_save()

    def can_turn_off(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        return (
            state is not None
            and state.domain in TURN_OFF_DOMAINS
            and state.state == STATE_ON
        )

    async def async_turn_off(self, entity_id: str, context: Context | None) -> None:
        """Turn it off, and keep the state it had so an undo can restore it."""
        state = self.hass.states.get(entity_id)
        if state is None or not self.can_turn_off(entity_id):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="house_check_cannot_turn_off",
                translation_placeholders={"entity_id": entity_id},
            )
        self.undo[entity_id] = {
            "state": state.state,
            "attributes": dict(state.attributes),
        }
        self._close_cards(lambda ids: entity_id in ids)
        await self._async_save()
        await self.hass.services.async_call(
            state.domain,
            "turn_off",
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
            context=context,
        )

    async def async_undo(
        self, entity_ids: list[str] | None, context: Context | None
    ) -> list[str]:
        """Put back what the cards turned off. All of it, or only these."""
        wanted = entity_ids if entity_ids else list(self.undo)
        states = [
            State(entity_id, saved["state"], saved["attributes"])
            for entity_id in wanted
            if (saved := self.undo.get(entity_id)) is not None
        ]
        await async_reproduce_state(self.hass, states, context=context)
        for state in states:
            del self.undo[state.entity_id]
        await self._async_save()
        return [state.entity_id for state in states]
