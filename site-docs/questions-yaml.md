# Questions in YAML

The UI and `configuration.yaml` work side by side. YAML is not deprecated, and
nothing migrates behind your back.

```yaml
jev:
  - name: Laundry
    scan_interval: 300
    entities:
      - sensor.washing_machine_power
      - binary_sensor.laundry_door
    questions:
      - name: Laundry forgotten
        type: noul
        instructions: Is the laundry finished but still sitting in the machine?
        background: >-
          This machine draws under 5 W when idle and over 300 W while a programme runs.
        threshold: 0.7
      - name: Nudge urgency
        type: score
        instructions: How urgently should someone be reminded?
        criteria: [Not at all, When convenient, Right now]
```

## Context keys

| Key | Required | Description |
|---|---|---|
| `name` | yes | Names the context and prefixes its entities |
| `entities` | one of these two | Entities, devices, areas, floors or labels to read |
| `state` | one of these two | Text or a template, alone or as a note beside the entities |
| `scan_interval` | no | Seconds between evaluations, minimum 30, default 300 |
| `trigger_entities` | no | Wake on these instead of on whatever `entities` names |
| `include_attributes` | no | Send every attribute of the picked entities, off by default |
| `questions` | yes | The questions asked about this state |

## Question keys

| Key | Required | Description |
|---|---|---|
| `name` | yes | Names the entity |
| `type` | yes | `noul`, `choice` or `score` |
| `instructions` | yes | The question. Takes a string, an object or a list |
| `criteria` | for choice and score | A mapping for choice, an ordered list for score |
| `background` | no | Standing facts, folded into the question |
| `true_means` / `false_means` | no | Noul only |
| `threshold` | no | Noul only. Also creates a binary sensor |

## One context is one call

A context is one API call. Keep related questions together and they cost tokens
rather than time: three questions measured 712 ms and a hundred measured 714.

Splitting them into two contexts costs a whole extra call, and doubles the latency
you wait for.

!!! note "This is the thing the UI does for you"
    In the UI there is no context to declare, because the grouping is derived from
    what each question points at. In YAML you declare it yourself, which is more
    control and more rope.

## Structured instructions

`instructions` and every criteria value accept an object or an array as well as a
string:

```yaml
      - name: Caller
        type: choice
        instructions:
          question: Who is at the door?
          examples:
            - a courier reading out a tracking number
            - somebody asking whether you have considered solar panels
        criteria:
          delivery:
            what: A courier dropping off or collecting a parcel
            not_for: Somebody asking you to sign a petition
```

Measured on five deliberately ambiguous doorbell callers, three runs each, this
bought nothing: 12 of 15 either way, mean confidence 0.90 flat against 0.87
structured. It is supported, and worth reaching for only when two options genuinely
blur and a plain sentence has already failed. See [Measurements](measurements.md).
