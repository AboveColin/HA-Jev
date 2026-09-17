# Jev for Home Assistant

Ask [TypeSafe Jev](https://typesafe.ai) questions about your house and get numbers
back. Jev is a decision model rather than a chat assistant. It answers a typed
question with a probability, a choice or a score, and this integration turns each
answer into an entity you can automate on.

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
          message: The washing is done and still in the machine.
```

That binary sensor exists because you asked one question in YAML. No template
gymnastics across six entity states, and no prompt to keep tuning.

## Install

Needs Home Assistant 2026.9 or newer, and an API key from
[typesafe.ai](https://typesafe.ai).

### Through HACS

It is not in the HACS default list yet, so it has to be added as a custom repository
once. [hacs/default#11052](https://github.com/hacs/default/pull/11052) is queued with
all twelve checks passing; when it merges these first two steps go away and Jev shows
up in a HACS search like anything else.

1. Open HACS, then the three dot menu at the top right, then **Custom repositories**.
2. Paste `https://github.com/AboveColin/HA-Jev`, set Type to **Integration**, and
   select **Add**.
3. Search HACS for **Jev**, open it, and select **Download**.
4. Restart Home Assistant.

Steps 1 and 2 in one click, if your Home Assistant is reachable from this browser:

[![Open your Home Assistant instance and open a repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=AboveColin&repository=HA-Jev&category=integration)

### By hand

Copy `custom_components/jev` from the
[latest release](https://github.com/AboveColin/HA-Jev/releases/latest) into your
`config/custom_components/` directory and restart Home Assistant. HACS will not
manage updates for a copy installed this way.

### Then set it up

Settings, Devices and services, Add integration, then **Jev (TypeSafe)**. It asks for
the API key and nothing else, and checks it by asking one short question before the
entry is created, so a bad key fails here rather than silently later.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=jev)

## Four actions

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

| Action | Ask | Get back |
|---|---|---|
| `jev.noul` | a yes/no question | `noul` 0 to 1, and `is_true` against your threshold |
| `jev.choice` | `options:`, two to 255 of them | `choice`, `probabilities`, `confidence` |
| `jev.score` | `levels:`, two to 10, lowest first | `score`, `normalized`, `nearest_level`, `legend`, `probabilities`, `confidence` |
| `jev.ask` | any mix, keyed by your own names | the same, under `answers` |

Every field arrives filled in, so the first run is an edit of a worked example.
Questions in one request are judged independently and answered in parallel, so three
took 712 ms and a hundred took 714 ms. Adding a question costs tokens, not time. Ask
everything at once.

Three things change the answers you get. Use one noul per label when several labels
can be true at once. Include a catch-all option, because the model can only pick from
the list you give it. And describe a situation per score level rather than a degree,
since "Broken, but there is a workaround" gives the model something to match and
"Moderately severe" does not.

## Point it at entities instead of writing the text

Every action takes a target, so pick entities, devices, areas, floors or labels in
the normal picker and skip the template. The integration sends a JSON object, which
is the shape the TypeSafe docs ask for, because the model reads field names as
labels:

```json
{"now": "2026-09-17 09:16 Thursday",
 "entities": [{"entity_id": "sensor.washing_machine_power", "name": "Washing machine power",
               "state": "1.2", "unit_of_measurement": "W", "device_class": "power",
               "area": "Laundry room", "changed": "14 minutes ago"}]}
```

Unavailable and unknown go through as they are, because a sensor that stopped
reporting is often the answer rather than a gap to paper over.

Add `state:` as well and your text arrives as a `note` beside the readings. That
pairing is worth more than it looks: the same question about the same two entities
returned 0.31 with the readings alone and 0.80 after one sentence of context.

An entity costs about 66 input tokens, measured. One made a 339 token request, five
made 559, ten made 931. A target is capped at 250 entities, roughly 16,500 tokens or
$0.0007 per evaluation, which stops somebody pointing a one-minute context at the
whole house. `include_attributes` sends every attribute too, and is off by default
because a weather forecast runs to thousands of tokens on every evaluation.

## Tell it how to read the numbers

This is the single change that moves answers the most. Jev makes a judgment and does
not compare numbers. Hand it a reading with the rule buried in surrounding prose and
it will not work out which side of the threshold the reading falls on.

Measured on the same question, five runs per cell, at 1.2 W and 1450 W:

| | idle | running | separation |
|---|---|---|---|
| the readings alone | 0.47 | 0.26 | +0.21 |
| the rule in `background:` | 0.70 | 0.10 | +0.60 |
| the comparison done in the template | 0.74 | 0.06 | +0.69 |
| both | 0.72 | 0.04 | +0.68 |

Either fix roughly triples the separation and they do not stack, so do one.
`background:` needs no Jinja, and it travels with the question rather than with the
readings, which is most of the effect: the same sentence put in the state measured
+0.33. Pass an object instead of a sentence and your own key names are kept, which
is worth doing because the model reads them.

Those are means of five runs. Repeated runs wander by around 0.15, so treat the gaps
as the finding rather than the digits.

## Automation variables and trigger data

Everything an automation knows can go into a question, because Home Assistant
renders action data before the action sees it. That covers `trigger`, a `variables:`
block and `this`. A variable holding a mapping stays a mapping, so
`household.residents` arrives as a list rather than a stringified dict.

```yaml
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

## Turn questions into entities

Questions in `configuration.yaml` become permanent entities, evaluated on a schedule
or when the things they watch change.

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

A noul sensor holds a probability, a score sensor holds a number that can land
between levels, and a choice sensor holds one of your options with the distribution
in its attributes. Add `threshold:` to a noul and you also get a binary sensor to
trigger on.

A context that names entities is woken by those entities. `entities:` also takes the
full picker form, so `area_id:` or `device_id:` work, and targeting an area means an
entity added later starts waking the context on its own. Write `state:` instead when
the wording matters, or write both. One context is one request, so keep related
questions together.

## Examples

Thirteen worked files in [examples/](examples/), each self-contained and
copy-pasteable. The test suite walks all of them: the YAML has to parse, any `jev:`
block has to pass the real config schema, and none may name a real house.

| | |
|---|---|
| [01 laundry reminder](examples/01_laundry_reminder.yaml) | the smallest useful thing: one question, one threshold, one binary sensor |
| [02 alert triage](examples/02_alert_triage.yaml) | three questions in one call, three notification paths |
| [03 doorbell triage](examples/03_doorbell_triage.yaml) | a choice on an intercom transcript, acted on in 300 ms |
| [04 situation layer](examples/04_situation_layer.yaml) | named situations every other automation can trigger on |
| [05 confidence gating](examples/05_confidence_gating.yaml) | act, ask, or stay quiet, decided by confidence |
| [06 composite score](examples/06_composite_score.yaml) | several scores combined with weights you own |
| [07 Jev gates the LLM](examples/07_llm_jev_gate.yaml) | a cheap typed decision in front of an expensive call |
| [08 cascade](examples/08_llm_cascade.yaml) | Jev answers the ordinary cases, low confidence escalates |
| [09 guardrail](examples/09_llm_guardrail.yaml) | the LLM writes, Jev checks the draft against the source |
| [10 extract then verify](examples/10_llm_extract_verify.yaml) | the LLM pulls fields out, Jev verifies each against the text |
| [11 post and parcels](examples/11_post_and_parcels.yaml) | one attention queue across several channels |
| [12 energy window](examples/12_energy_window.yaml) | where to keep the arithmetic and where to ask |
| [13 voice commands](examples/13_voice_commands.yaml) | a command router: 12 questions in one request, three of them read |

Four combine Jev with a language model through `ai_task.generate_data`, which works
with Google Generative AI, OpenAI, Anthropic or a local Ollama. The division of
labour is the same in all four. Jev decides in about 300 ms for a fraction of a cent
and returns something code can branch on, and the model writes prose or handles what
Jev was unsure about.

The voice command router is the largest, following TypeSafe's own
[smart home demo](https://docs.typesafe.ai/demos/smart-home). Its device options come
from your own entity registry, so the answer is an `entity_id` you can act on with no
mapping table.

## What it costs, and the budget

Calls today, input tokens today and estimated cost today are entities. The token
counts come from the API and the money is an estimate, because the price per million
is a setting. At $0.042 per million, a context of 450 tokens evaluated every five
minutes costs about 15 cents a year.

Set a daily input token budget in the options. It is a tripwire, so put it far past
anything a working setup would use. When it trips, evaluation stops, answers keep
their last value, `binary_sensor.jev_daily_budget_exceeded` turns on, and a repair
notice names the budget and what was used. The totals survive a restart, because a
budget a restart clears is not a budget.

## Configuration

| Option | Where | Default | What it does |
|---|---|---|---|
| API key | config flow | none | Checked with one short question before the entry is created |
| Daily input token budget | options | 0 | Stops evaluating for the day once this many input tokens are spent. 0 means no limit |
| Price per million input tokens | options | 0.042 | Only affects the estimated cost sensor |

| `jev:` key | Required | What it does |
|---|---|---|
| `name` | yes | Names the context and prefixes its entities |
| `entities` | one of these two | Entities, devices, areas, floors or labels to read |
| `state` | one of these two | Text or a template, alone or as a note beside the entities |
| `scan_interval` | no | Seconds between evaluations, minimum 30, default 300 |
| `trigger_entities` | no | Wake on these instead of on whatever `entities` names |
| `include_attributes` | no | Send every attribute of the picked entities, off by default |
| `questions` | yes | Each with `name`, `type`, `instructions`, and `criteria` for choice and score |

Nothing polls on its own. A question is evaluated when a context reaches its
`scan_interval`, when an entity it watches changes, or when an automation calls an
action. Entity changes are debounced by 5 seconds and `scan_interval` has a floor of
30, because every evaluation is a paid call and a flapping sensor must not spend
money in a loop.

Use Reconfigure to change the API key, which keeps your entities and their history.

## Troubleshooting

Turn on debug logging first. It prints the type and full content of every state
sent, which is almost always the answer:

```yaml
logger:
  logs:
    custom_components.jev: debug
```

**An answer looks wrong or barely moves.** Read the state in the log. Usually it
does not contain what you assumed, or it contains a number the model is being asked
to compare against a threshold.

**Every answer sits near 0.5 with low confidence.** The question is measuring more
than one thing. Split it and combine the parts in your own template.

**Entities unavailable, budget sensor on.** The daily budget stopped evaluation.

**Entities unavailable, budget sensor off.** Look for a line saying TypeSafe is not
answering, logged once when the outage starts and once when it ends.

**Setup fails with "TypeSafe did not answer".** Setup proves the service answers
before creating any entity, so this is connectivity rather than configuration. Home
Assistant retries on its own.

**An error names a limit.** It names the number you gave as well. A choice takes 2
to 255 options, a score 2 to 10 levels, a target at most 250 entities.

## What this will not do

It does not explain itself. An answer is a number with no reasoning attached, so
anything you need to audit later needs its evidence recorded elsewhere.

Confidence is not calibrated. TypeSafe publishes no calibration evidence and its own
docs call the value a convenient default. Treat 0.9 as higher than 0.6 rather than
as right nine times in ten, until you have measured it on your own questions.

It is slower from here than the published 70 to 500 ms. Measured from the
Netherlands across 16 calls, a warm connection answers in 250 to 580 ms, a first
call after an idle spell takes 700 to 900 ms, and 400 questions took 1.3 s. Fine for
a doorbell, too slow for a tight loop.

Do not put it in front of a safety decision. A probability with no explanation is
not the right thing to hold a lock, a heater or a smoke alarm.

## Tests and quality

```
pip install -r requirements-test.txt
pytest
```

101 tests run the integration inside a real Home Assistant through
`pytest-homeassistant-custom-component` with the API client replaced, so the suite
spends nothing. Coverage is 96 percent, and 100 percent on the config flow.

`quality_scale.yaml` records this integration against Home Assistant's quality scale
rule by rule: 47 done and 7 exempt with a stated reason, out of 54. Graded tiers only
go to integrations inside core, so a custom integration scores Custom, but the file
is what a core submission needs. All three Platinum rules are met, including
`mypy --strict` clean on 12 modules and enforced in CI.

## Not affiliated with TypeSafe

Independent integration. The API client is
[jevclient](https://github.com/AboveColin/jevclient).
