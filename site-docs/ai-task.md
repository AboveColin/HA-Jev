# AI Task

Jev registers an AI Task entity, `ai_task.jev`. Call it from a script or an
automation and you get the answer back in the same step, instead of waiting for a
sensor to catch up.

```yaml
- action: ai_task.generate_data
  response_variable: triage
  data:
    task_name: doorbell triage
    entity_id: ai_task.jev
    instructions: >-
      Transcript: {{ states('sensor.intercom_transcript') }}
      Nobody is expected today.
    structure:
      caller:
        description: Who is at the door?
        selector:
          select:
            options: [delivery, visitor, cold caller]
      urgency:
        description: >-
          How urgently does somebody need to go to the door,
          0 not at all and 10 immediately?
        selector:
          number:
            min: 0
            max: 10
            step: 1
- if: "{{ triage.data.urgency >= 7 }}"
  then:
    - action: notify.mobile_app
      data: { message: "{{ triage.data.caller }} at the door, worth going." }
```

`triage.data.caller` is one of the three options you wrote. `triage.data.urgency` is
a number between 0 and 10.

## When to use it instead of a question

A [question](questions-ui.md) is evaluated on a schedule or when its entities change,
and lands in a sensor. That is the right shape when you want the answer over time,
want history, or want to trigger on it.

Inside a script it is the wrong shape. A trigger wakes the context through a 5 second
debounce, so the sensor can still hold the answer from before the change that started
the script. The AI Task asks at the moment you call it.

Against [`jev.ask`](actions.md), which also answers on the spot, the difference is who
writes the question. `jev.ask` takes Jev's own shape, with `instructions`, `options`
and `levels`. The AI Task takes a Home Assistant structure, the same block you would
hand to an LLM task, so an automation can be pointed at either without being rewritten.
If you are not switching providers, `jev.ask` is the more direct route and carries no
mapping.

## What a field becomes

The `selector:` of each field decides which question is asked. Three selectors map
onto the three answers and nothing else does.

| Field selector | Question | What lands in `data` |
|---|---|---|
| `boolean` | a [noul](primitives.md) | `true` at probability 0.5 or above, else `false` |
| `select` with 2 to 255 options | a choice | the option value, as you wrote it |
| `number` with `min` and `max` | a score | a number on your own scale |

The field's `description:` is the question. A field without one asks about its own
name, which is worth something when the name is `window_open` and nothing when it is
`value`. Write the description.

`required:` makes no difference here. Every field is asked and every field comes back,
because a reply missing one is an error rather than a gap.

### Numbers

A score is rated against described levels, so the scale is cut into rungs and each
rung is labelled with the number it stands for. Jev takes at most 10 levels, so
`min: 0, max: 100, step: 1` is ten rungs rather than a hundred and one:

```
0, 11, 22, 33, 44, 56, 67, 78, 89, 100
```

Every rung is a value your field would accept, and the answer comes back as one of
them. Leaving `step` out, or setting it to `any`, gives ten rungs and no snapping.
`unit_of_measurement` is put on the labels, so a 16 to 24 `°C` field is rated against
`16°C` through `24°C` rather than against bare numbers.

A number field is the one most worth writing a long description for. The rungs carry
the numbers, and nothing else says what a 7 means.

## The confidence comes back under `jev`

`ai_task.generate_data` returns the fields and nothing else, so a boolean arrives as a
bare `true` with no sign of how sure Jev was. Confidence is the number that separates
an answer worth acting on from a guess, so it travels beside your fields under the key
`jev`:

```yaml
- if: >-
    {{ triage.data.urgency >= 7
       and triage.data.jev.answers.urgency.confidence > 0.6 }}
```

| Key | What it holds |
|---|---|
| `jev.model` | The model that answered |
| `jev.input_tokens` | What the request cost, as the API reported it |
| `jev.latency_ms` | Round trip in milliseconds |
| `jev.answers.<field>.probability` | Boolean fields only: the raw probability before the 0.5 cut |
| `jev.answers.<field>.confidence` | Select and number fields |
| `jev.answers.<field>.probabilities` | Select and number fields: the full distribution |
| `jev.answers.<field>.nearest_level` | Number fields: the rung it picked, with its unit |

A field of your own named `jev` is refused rather than overwritten.

## What it refuses, and why it costs nothing

Every refusal below happens before the request, so a badly shaped task spends nothing.

| Refusal | Fix |
|---|---|
| A task with no `structure` | Jev answers typed questions only. Add one |
| A structure with no fields | Add one |
| A field using any other selector | Use a boolean, a select or a number |
| A number with no `min` or no `max` | A rating needs both ends of the scale |
| A select with fewer than 2 options | 2 to 255 |
| A field named `jev` | Rename it |

The [daily budget](cost.md#the-budget-is-a-tripwire) is checked the same way. The size
of the request is measured and turned into a token estimate, and a task that would not
fit in what is left of the budget is refused before it is sent, naming the estimate and
what remains.

## What it does not read

The AI Task belongs to the integration, not to a context, so your configured questions
and their targets play no part. Whatever you put in `instructions` is the whole state
Jev judges. Build it with templates, the same way you would build the `state` of an
[action](actions.md).

Its calls, tokens and cost count against the same daily totals as everything else.
