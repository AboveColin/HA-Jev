"""What the house looks like to Assist.

Only entities the user exposed to Assist are described here, and nothing else is
ever sent to TypeSafe. That boundary is the user's, not ours: they already decided
which entities a voice assistant may see, and a question is not a reason to widen it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.components.conversation import DOMAIN as CONVERSATION_DOMAIN
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr

# Domains a spoken command can act on through a built-in intent. Anything else is
# left to the fallback agent rather than half-handled here.
CONTROLLABLE = (
    "light",
    "switch",
    "fan",
    "cover",
    "lock",
    "media_player",
    "climate",
    "vacuum",
    "input_boolean",
    "scene",
    "script",
)


@dataclass(slots=True)
class ExposedEntity:
    """One entity, as the model will see it."""

    entity_id: str
    name: str
    domain: str
    area: str | None
    state: str

    def as_option(self) -> str:
        where = f", in the {self.area}" if self.area else ""
        return f"{self.name}{where} ({self.domain}, currently {self.state})"


@dataclass(slots=True)
class HomeSnapshot:
    """Everything one request is allowed to know about the house."""

    entities: list[ExposedEntity] = field(default_factory=list)
    areas: list[str] = field(default_factory=list)
    floors: list[str] = field(default_factory=list)

    @property
    def domains(self) -> list[str]:
        return sorted({e.domain for e in self.entities})

    def by_id(self, entity_id: str) -> ExposedEntity | None:
        return next((e for e in self.entities if e.entity_id == entity_id), None)

    def in_area(self, area: str, domain: str | None = None) -> list[ExposedEntity]:
        return [
            e
            for e in self.entities
            if e.area == area and (domain is None or e.domain == domain)
        ]

    def as_state(self) -> dict[str, object]:
        """The shape sent to TypeSafe, with field names the model reads as labels."""
        return {
            "entities": [
                {
                    "entity_id": e.entity_id,
                    "name": e.name,
                    "domain": e.domain,
                    "area": e.area or "unassigned",
                    "state": e.state,
                }
                for e in self.entities
            ],
            "areas": self.areas,
            "floors": self.floors,
        }


@callback
def async_snapshot(hass: HomeAssistant, limit: int) -> HomeSnapshot:
    """Collect the exposed, controllable entities, newest registry state."""
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    areas = ar.async_get(hass)
    floors = fr.async_get(hass)

    def area_of(entity_id: str) -> str | None:
        entry = entities.async_get(entity_id)
        if entry is None:
            return None
        area_id = entry.area_id
        if area_id is None and entry.device_id:
            device = devices.async_get(entry.device_id)
            area_id = device.area_id if device else None
        if area_id is None:
            return None
        area = areas.async_get_area(area_id)
        return area.name if area else None

    found: list[ExposedEntity] = []
    for state in hass.states.async_all(CONTROLLABLE):
        if not async_should_expose(hass, CONVERSATION_DOMAIN, state.entity_id):
            continue
        found.append(
            ExposedEntity(
                entity_id=state.entity_id,
                name=state.name,
                domain=state.domain,
                area=area_of(state.entity_id),
                state=state.state,
            )
        )
    # Sorted so the option list is stable between requests, which makes a trace
    # readable when the same command is tried twice.
    found.sort(key=lambda e: e.entity_id)

    return HomeSnapshot(
        entities=found[:limit],
        areas=sorted({a.name for a in areas.async_list_areas()}),
        floors=sorted({f.name for f in floors.async_list_floors()}),
    )
