# The house check

`jev.house_check` looks for things in the house that are probably wrong and opens a
Repairs card for each one. Each card gives you a choice. You can ignore it, snooze it
for 30 days or, for a light, switch or fan that is on, turn it off.

## What it checks

| Check | Finds | Costs |
|---|---|---|
| Unavailable | An entity that was unavailable for all of the last 7 days | Nothing |
| Low battery | A battery sensor below 10 %, or a battery binary sensor that is on | Nothing |
| Looks wrong | A state that Jev thinks someone would want to fix now | One request |

The first two checks read Home Assistant and the recorder. They do not call TypeSafe.
The unavailable check reads the recorder for the whole week, so a device that dropped
off for an hour yesterday does not get a card. The recorder keeps 10 days unless you
changed `purge_keep_days`, so the week fits.

The third check sends one request for the whole house. It describes each entity that
you exposed to Assist and that Jev can control, up to 150: its name, its kind, its area, its state and how many minutes
that state has been unchanged, together with the day and the time. It then asks one
yes/no question for each entity: "Is its state a mistake that someone in the house
would want to fix now, given the time and the rest of the house?" A card opens when the
answer is 0.8 or more. Scenes and scripts are in the description but are not asked
about, because their state cannot be left wrong.

## Run it

```yaml
- action: jev.house_check
  response_variable: check
  data:
    use_jev: true
```

| Field | Default | Meaning |
|---|---|---|
| `use_jev` | `true` | Off runs only the two checks that cost nothing |
| `config_entry` | | Which API key to ask with. Only needed when you set up more than one |

It returns the findings:

```yaml
findings:
  - kind: unavailable
    name: Garden sensors
    entity_ids: [sensor.garden_temperature, sensor.garden_humidity, sensor.garden_soil]
    state: unavailable
    value: "7"
  - kind: looks_wrong
    name: Porch light
    entity_ids: [light.porch]
    state: "on"
    value: "0.91"
```

`value` is the number of days for an unavailable card, the level for a battery card and
the probability for a card that looks wrong.

To run it once a week, go to **Settings**, **Devices & services**, **Jev**,
**Configure**, then **Check the house once a week**. It then runs at 10:00 on the first
morning that is 7 or more days after the last full check. If that run cannot reach Jev,
for example because the daily budget is spent, the two free checks still open their
cards, and one more card says why the Jev check did not run.

## The cards

An integration that loses its device or its server makes every one of its entities
unavailable. So the unavailable entities of one integration share one card, and that
card names the integration, gives the count and shows three of the entities as
examples. On one test instance, 518 unavailable entities made 5 cards, and 438 of
those entities came from one integration. An entity that no integration owns, such as
one from YAML, gets a card of its own.

| Choice | What it does |
|---|---|
| Ignore | Never report the entities on this card again |
| Snooze | Report them again after 30 days |
| Turn off | Turn the entity off, and keep its state so you can undo it |

Turn off is only on a card for a single light, switch or fan that is on. The house
check never changes a lock, a cover, a climate entity or a valve, because a change that
nobody watches can let someone in or let a pipe freeze.

Ignore and snooze apply to the entities that were on the card when you chose. An entity
of the same integration that becomes unavailable later gets a new card.

A card closes by itself when the next run that checks for it no longer finds it. A
run without Jev leaves the cards that only Jev can find open. The cards stay after a
restart. When you remove the Jev entry, its cards go with it.

## Undo a turn-off

```yaml
- action: jev.undo_house_check
  response_variable: undo
  data:
    entity_id: light.porch
```

| Field | Default | Meaning |
|---|---|---|
| `entity_id` | all | Only these. Empty puts back everything the house check turned off |
| `config_entry` | | Which API key it was. Only needed when you set up more than one |

The action puts back the state the entity had before, brightness included,
and returns the entities it restored as `restored`.

## What it costs

A run without Jev costs nothing. A run with Jev is one request. On a test instance with
6 entities exposed to Assist, of which 2 were asked about, it cost 773 input tokens. See
[Measurements](measurements.md#the-house-check).
