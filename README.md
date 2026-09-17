# Jev for Home Assistant

Ask [TypeSafe Jev](https://typesafe.ai) questions about your house and get numbers
back. Not a chat assistant: Jev is a decision model, so it answers a typed question
with a probability, a choice or a score, and Home Assistant turns each answer into
an entity you can automate on.

```yaml
automation:
  - alias: Remind about the washing
    triggers:
      - trigger: state
        entity_id: binary_sensor.jev_laundry_forgotten
        to: "on"
        for: "00:10:00"
    actions:
      - action: notify.mobile_app
        data:
          message: "The washing is done and still in the machine."
```

That binary sensor exists because you asked one question in YAML. No template
gymnastics over six entity states, and no prompt to keep tuning.

## Install

Needs Home Assistant 2026.9 or newer. HACS, as a custom repository, until the
integration is in the default list. Add
`https://github.com/AboveColin/HA-Jev` as an Integration, install, restart, then add
**Jev (TypeSafe)** from Settings, Devices and services. It asks for an API key and
checks it by asking one short question.

## Ask questions

Questions live in `configuration.yaml`, grouped into contexts. A context is one
piece of text plus everything you want to know about it.

A context can name entities instead of a template, in which case those same
entities are also what wakes it:

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
        instructions: Is the washing machine idle, suggesting the programme has finished?
        threshold: 0.7
```

`entities:` also takes the full picker form, so `area_id: laundry_room` or
`device_id:` work the same way. Targeting an area rather than a list means an
entity added to that area later starts waking the context on its own.

Write the text yourself when the wording matters:

```yaml
jev:
  - name: Laundry
    scan_interval: 300
    trigger_entities:
      - sensor.washing_machine_power
    state: >
      Washing machine power draw: {{ states('sensor.washing_machine_power') }} W.
      Door closed: {{ states('binary_sensor.laundry_door') }}.
      The programme finished {{ relative_time(states.sensor.washing_machine_finished.last_changed) }} ago
      and nobody has entered the room since.
    questions:
      - name: Laundry forgotten
        type: noul
        instructions: Is the laundry finished but still sitting in the machine?
        "true": The programme is done and nobody has emptied it
        "false": Still running, or already emptied
        threshold: 0.7

      - name: Nudge urgency
        type: score
        instructions: How urgently should someone be reminded?
        criteria:
          - Not at all
          - When convenient
          - Right now

      - name: Room
        type: choice
        instructions: Which room does this concern?
        criteria:
          laundry: Washer and dryer
          kitchen: Cooking and dishwasher
          bathroom:
```

That produces `sensor.jev_laundry_forgotten` (a probability from 0 to 1),
`binary_sensor.jev_laundry_forgotten` (the probability against your threshold),
`sensor.jev_nudge_urgency` (a number between the levels) and `sensor.jev_room` (one
of your options, with the full distribution in its attributes).

### The three question types

| Type | What you ask | The entity state | Attributes |
|---|---|---|---|
| `noul` | a yes/no question | probability of yes, 0 to 1 | none, the probability is the whole answer |
| `choice` | pick one of your options | the winning option | `probabilities`, `confidence` |
| `score` | rate against ordered levels | a number, which may fall between levels | `probabilities`, `confidence`, `legend`, `nearest_level` |

Add `threshold:` to a noul and you also get a binary sensor, so an automation can
trigger on a state change instead of re-deciding the threshold in a template.

### One context is one request

Every question in a context is evaluated in isolation against the same text, and
the API answers them in parallel. Measured from the Netherlands: three questions
took 712 ms and a hundred took 714 ms, a difference of 24 ms for 97 more questions.
So put related questions in one context and they cost almost nothing extra in time.

They do cost money. Question text is billed as input, roughly 38 tokens for a short
one, so a hundred questions is a few thousand tokens per evaluation rather than a
few hundred.

## Point it at entities instead of writing a template

Every action takes a target, so you can pick entities, devices, areas, floors or
labels in the normal Home Assistant picker and skip the template entirely. The
integration turns what you picked into a JSON object and sends that.

```yaml
- action: jev.noul
  target:
    area_id: laundry_room
  data:
    instructions: Is the washing machine idle, suggesting the programme has finished?
```

What the model receives for that looks like this, which is the shape the TypeSafe
docs ask for, because the model reads field names as labels:

```json
{
  "now": "2026-09-17 09:16 Thursday",
  "entities": [
    {"entity_id": "sensor.washing_machine_power", "name": "Washing machine power",
     "state": "1.2", "unit_of_measurement": "W", "device_class": "power",
     "area": "Laundry room", "changed": "14 minutes ago"}
  ]
}
```

Unavailable and unknown states go through as they are, because a sensor that has
stopped reporting is often the answer rather than a gap to paper over.

Add `state:` as well as a target and your text arrives as a `note` field beside the
readings. That combination is usually the right one, and it is worth more than it
looks. Asking "is the laundry finished but still sitting in the machine" about a
power sensor and a door sensor returned 0.31. Adding one sentence saying the
programme finished 14 minutes ago, same question and same two entities, returned
0.80. Twenty-five tokens of context bought that.

Each entity costs about 66 input tokens, measured against the live API: 1 entity
made a 339 token request, 5 made 559 and 10 made 931. A target is capped at 250
entities, which is roughly 16,500 tokens or $0.0007 per evaluation. That cap exists
to stop somebody pointing a one-minute context at the whole house, not to ration
normal use.

`include_attributes` sends every attribute rather than just the value, unit, device
class and area. Leave it off unless you need it: a weather forecast or a media
player's artwork list runs to thousands of tokens on every single evaluation.

## Four actions, and three of them need no YAML at all

Most automations ask one question. Those get their own action, with every field
filled in when you open it, so the first useful run is an edit of the example
rather than a blank object editor.

**`jev.noul`** asks a yes/no question and returns the probability of yes.

```yaml
- action: jev.noul
  response_variable: laundry
  data:
    state: |
      Washing machine: {{ states('sensor.washing_machine_power') }} W
      Finished: {{ relative_time(states.sensor.washing_machine_finished.last_changed) }} ago
    instructions: Is the laundry finished but still sitting in the machine?
    true_means: The programme is done and nobody has emptied it
    false_means: Still running, or already emptied
    threshold: 0.7
- if: "{{ laundry.is_true }}"
  then:
    - action: notify.mobile_app
      data: { message: "The washing is done and still in the machine." }
```

Returns `noul` (0 to 1), `is_true` against your threshold, and `threshold`. Use one
noul per label when several labels can be true at once, rather than a choice.

**`jev.choice`** picks one of your options and returns the whole distribution.

```yaml
- action: jev.choice
  response_variable: caller
  data:
    state: "{{ trigger.payload }}"
    instructions: What kind of caller is at the door?
    options: [delivery, neighbour, sales, other]
    option_descriptions:
      delivery: A parcel or food delivery
      sales: Selling energy, internet or a charity subscription
- if: "{{ caller.choice == 'sales' and caller.confidence > 0.8 }}"
  then: [...]
```

Returns `choice`, `probabilities` and `confidence`. Options are limited to 255.
Include a catch-all such as `other`, because the model can only pick from the list
you give it.

**`jev.score`** rates against levels you describe.

```yaml
- action: jev.score
  response_variable: urgency
  data:
    state: "{{ trigger.payload }}"
    instructions: How urgently must someone act on this?
    levels:
      - Nothing to do, it resolved itself
      - Worth looking at this week
      - Needs attention today
      - Wake someone up now
```

Returns `score` (0 to the number of levels minus one), `normalized` (the same score
as 0 to 1, which is what you need before weighting several scores together),
`nearest_level`, `legend`, `probabilities` and `confidence`.

Two rules that change the answers you get. Describe a situation per level, not a
degree: "Broken, but there is a workaround" gives the model something to match and
"Moderately severe" does not. And keep one dimension per question, because a level
that says punctual and clever and experienced measures three things, so a mixed
input cannot be placed anywhere and the confidence collapses. Levels are limited to
10, and each one is judged on its own, so comparative wording and numbers written
into the text do nothing.

**`jev.ask`** is the one to reach for when you want several answers about the same
text. Every question is judged independently and they run in parallel, so twenty
questions take about as long as one.

```yaml
- action: jev.ask
  response_variable: jev
  data:
    state: |
      Front door: {{ states('binary_sensor.front_door') }}
      Nobody home since: {{ relative_time(states.person.colin.last_changed) }}
      Outside: {{ states('sensor.outside_temperature') }} degrees
    questions:
      warn:
        type: noul
        instructions: Should someone be warned about this?
      urgency:
        type: score
        instructions: How urgently must someone act?
        criteria: [Nothing to do, Worth looking at this week, Needs attention today]
- if: "{{ jev.answers.warn.noul > 0.8 }}"
  then: [...]
```

The keys are yours. They come back as the keys of `answers` and the model never
sees them, so name them for your code.

All four actions accept a template in `state` and render it before sending. They
also take an object or a list there, not only a string, which is worth doing when
the state has several named parts: the model reads JSON as labelled data rather
than as one blob.

## Automation variables and trigger data

Everything an automation knows can go into a question, with no special support
needed: Home Assistant renders action data before the action ever sees it. That
covers `trigger`, a `variables:` block, `this`, and anything else in scope.

```yaml
automation:
  - alias: Triage the doorbell
    triggers:
      - trigger: mqtt
        topic: intercom/transcript
    variables:
      household:
        residents: [Colin]
        expects_deliveries: true
    actions:
      - action: jev.choice
        response_variable: caller
        data:
          state:
            said: "{{ trigger.payload }}"
            time: "{{ now().strftime('%H:%M on %A') }}"
            household: "{{ household }}"
          instructions: What kind of caller is this?
          options: [delivery, neighbour, sales, other]
```

A variable holding a mapping stays a mapping. That example arrives as real JSON with
`household.residents` still a list, not as a stringified dict, which matters because
the model reads field names as labels. Verified in a running instance: the debug log
shows `asking 4 question(s) about a dict state: {'trigger_value': 'a ZEBRA walked
past', 'room': 'laundry', 'machine': {'brand': 'Miele', 'idle_watts': 5}}`, and
questions about `machine.brand` and `machine.idle_watts` answered correctly.

Turn on debug logging to see exactly what was sent:

```yaml
logger:
  logs:
    custom_components.jev: debug
```

Templates work the same way in `instructions`, `background`, `options` and `levels`,
because they are all just action data.

One limit worth knowing: a `jev:` context in `configuration.yaml` has no automation
around it, so it has no variables and no trigger. It has entities, a template, and
`background` on each question. Anything that needs a trigger's data belongs in an
automation calling an action.

## What it costs, and the budget

Three entities report spending: calls today, input tokens today, and estimated cost
today. The token counts come from the API. The money is an estimate, because the
price per million is a setting.

At TypeSafe's published $0.042 per million input tokens, a context of 450 tokens
evaluated every five minutes costs about 15 cents a year.

Set a **daily input token budget** in the integration options. It is a tripwire, so
put it far past anything a working setup would use. When it trips, evaluation stops,
answers keep their last value, `binary_sensor.jev_daily_budget_exceeded` turns on,
and a repair notice names the budget and what was used. The totals survive a restart,
because a budget a restart clears is not a budget.

## Tell it how to read the numbers

This is the single change that moves answers the most. Jev makes a judgment; it
does not compare numbers. Hand it a reading and bury the rule in the surrounding
prose, and it will not work out which side of the threshold the reading falls on.

There are two ways to fix that and they work about equally well. Measured on the
same question, five runs per cell, asking whether the laundry is finished but still
in the machine, at 1.2 W and at 1450 W:

| | idle | running | separation |
|---|---|---|---|
| the readings alone | 0.47 | 0.26 | +0.21 |
| the rule in a `background:` field | 0.70 | 0.10 | +0.60 |
| the comparison done in the template | 0.74 | 0.06 | +0.69 |
| both | 0.72 | 0.04 | +0.68 |

Either one roughly triples the separation, and they do not stack, so do one.

The `background:` field is the one that needs no Jinja:

```yaml
- action: jev.noul
  target:
    entity_id: sensor.washing_machine_power
  data:
    instructions: Is the laundry finished but still sitting in the machine?
    background: >-
      This machine draws under 5 W when idle and over 300 W while a programme runs.
```

It travels with the question, not with the readings. That placement is the part
that matters: the same sentence added to the state instead measured +0.33, roughly
half of what it is worth in the question.

Pass an object rather than a sentence and your own key names are kept, which is
worth doing because the model reads them:

```yaml
    background:
      how_to_read_the_power: Under 5 W means idle, over 300 W means a programme is running.
      what_counts_as_emptied: The door sensor opening after the programme ended.
```

The other route is to do the comparison in Jinja, where it is exact and free, and
hand over the conclusion in words:

```yaml
state: >-
  The washing machine is
  {% if states('sensor.washing_machine_power') | float(0) < 5 %}
  drawing almost no power, which means it is idle
  {% else %}
  drawing {{ states('sensor.washing_machine_power') }} W, so a programme is running
  {% endif %}.
```

A note on the numbers above: they are means of five runs, and repeated runs of the
same cell on different days wander by around 0.15. The ordering held across every
run; treat the gaps as the finding, not the digits.

## What this will not do

It does not explain itself. An answer is a number, with no reasoning attached, so
anything you need to audit later needs its evidence recorded elsewhere.

Confidence is not calibrated. TypeSafe publishes no calibration evidence, and its own
docs call the value a convenient default. Treat 0.9 as "higher than 0.6", not as
"right 90 percent of the time", until you have measured it on your own questions.

It is slower from here than TypeSafe's published 70 to 500 ms. Measured from the
Netherlands across 16 calls: a warm connection answers in 250 to 580 ms, the first
call after an idle spell takes 700 to 900 ms, and a request carrying 400 questions
took 1.3 s. Fine for a doorbell, too slow for anything in a tight loop.

Do not put it in front of a safety decision. A probability with no explanation is not
the right thing to hold a lock, a heater or a smoke alarm.

## Examples

[examples/](examples/) has twelve worked files, each self-contained and
copy-pasteable. Four of them combine Jev with a language model through
`ai_task.generate_data`, which works with Google Generative AI, OpenAI, Anthropic or
a local Ollama:

| | |
|---|---|
| [Jev gates the LLM](examples/07_llm_jev_gate.yaml) | a cheap typed decision in front of an expensive call, so the LLM only writes when there is something worth saying |
| [a cascade](examples/08_llm_cascade.yaml) | Jev answers the ordinary cases, and low confidence escalates to the model that can reason |
| [a guardrail](examples/09_llm_guardrail.yaml) | the LLM writes, Jev checks the draft against the source before it is sent |
| [extract then verify](examples/10_llm_extract_verify.yaml) | the LLM pulls fields out, Jev verifies each one against the text it came from |

The other eight cover a laundry reminder, alert triage, doorbell triage, a layer of
named situations, confidence gating, composite scoring, one attention queue across
channels, and where to keep arithmetic.

Every example is checked by the test suite: the YAML has to parse, any `jev:` block
has to pass the real config schema, and none of them may mention a real house.

## Tests

```
pip install -r requirements-test.txt
pytest
```

37 tests against a real Home Assistant instance through
`pytest-homeassistant-custom-component`, with the API client replaced, so nothing
in the suite spends a token. Coverage is 90 percent overall and 100 percent on the
config flow.

They cover the config flow including reauth and the check that the API key is never
used as a unique id, all four actions and everything they refuse, the state built
from picked entities, the daily budget stopping evaluation while the entities that
explain it stay available, usage surviving a reload, and diagnostics redacting the
key.

## Not affiliated with TypeSafe

Independent integration. The API client is [jevclient](https://github.com/AboveColin/jevclient).
