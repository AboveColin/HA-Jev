# Actions

Four actions answer inside an automation and return a response variable.

```yaml
- action: jev.noul
  response_variable: laundry
  target:
    entity_id: sensor.washing_machine_power
  data:
    instructions: Is the laundry finished but still sitting in the machine?
    background: >-
      This machine draws under 5 W when idle and over 300 W while a programme runs.
    threshold: 0.7
- if: "{{ laundry.is_true }}"
  then:
    - action: notify.mobile_app
      data: { message: The washing is done and still in the machine. }
```

| Action | You give it | You get back |
|---|---|---|
| `jev.noul` | a yes/no question | `noul` 0 to 1, `is_true` against your threshold |
| `jev.choice` | `options:`, 2 to 255 | `choice`, `probabilities`, `confidence` |
| `jev.score` | `levels:`, 2 to 10, lowest first | `score`, `normalized`, `nearest_level`, `legend`, `probabilities`, `confidence` |
| `jev.ask` | any mix, under your own keys | the same, under `answers` |

Every response also carries `usage` and `latency_ms`, so an automation can watch what
it spends.

## Point at entities instead of writing a template

All four take the normal Home Assistant target picker. Select entities, devices,
areas, floors or labels and the state is built for you:

```yaml
- action: jev.choice
  response_variable: room
  target:
    area_id: kitchen
  data:
    instructions: What is happening in this room?
    options:
      cooking: Somebody is preparing food
      eating: Somebody is at the table
      empty: Nobody is in here
```

Up to 250 entities. Past that it refuses and tells you your number, because
[every entity is billed on every evaluation](cost.md).

The actions send whatever you target, whether or not it is exposed to Assist. An
automation names its entities on purpose, so the Assist exposure list is not
consulted here; it is consulted for the [conversation agent](conversation.md).
`include_attributes: true` never sends access tokens, entity pictures or coordinates,
so a camera or a `device_tracker` is safe to target.

## More than one entry

Each Jev entry has its own key and budget. With more than one loaded, an action has
to name the one to ask with under **API key** (`config_entry:` in YAML). An action
that names none is refused before anything is sent, and the error lists the entries.

## Ask several things at once

`jev.ask` takes any mix under your own keys, in one request:

```yaml
- action: jev.ask
  response_variable: jev
  data:
    state:
      transcript: "{{ trigger.payload }}"
    questions:
      caller:
        type: choice
        instructions: Who is at the door?
        criteria:
          delivery: A courier
          visitor: Someone we know
      urgency:
        type: score
        instructions: How quickly does this need a person?
        criteria: [Never, Whenever, Right now]
      answer_now:
        type: noul
        instructions: Does someone need to go to the door right now?
```

This is the shape to reach for. Adding a question costs tokens, not time, so asking
the five you might need and discarding four is cheaper than two round trips.

## Templates and variables

`state:` takes a template, an object or a list, and automation variables reach it:

```yaml
- variables:
    household: "{{ states('input_text.house_notes') }}"
- action: jev.noul
  response_variable: answer
  data:
    state:
      note: "{{ household }}"
      time: "{{ now().strftime('%H:%M on %A') }}"
    instructions: Is this worth waking someone for?
```

An object arrives as an object, not as a stringified dict. The model reads the field
names as labels, so name them for what they hold.

## Tune a threshold

`jev.calibrate` is a fifth action that asks nothing. It compares the recorded history
of a noul sensor with an entity that shows what was really true, and returns the
threshold that fits best. It costs no tokens. See [tuning a threshold](calibrate.md).

## Errors name the limit

Every budget failure names the budget, the limit and your ask, because an agent
reading the error can fix `max 255 options, asked for 300` and cannot fix a blank
window.
