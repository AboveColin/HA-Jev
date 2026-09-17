[![Tests](https://github.com/AboveColin/HA-Jev/actions/workflows/tests.yaml/badge.svg)](https://github.com/AboveColin/HA-Jev/actions/workflows/tests.yaml)
[![hassfest](https://github.com/AboveColin/HA-Jev/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/AboveColin/HA-Jev/actions/workflows/hassfest.yaml)
[![HACS Action](https://github.com/AboveColin/HA-Jev/actions/workflows/hacs.yaml/badge.svg)](https://github.com/AboveColin/HA-Jev/actions/workflows/hacs.yaml)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/AboveColin/HA-Jev)](https://github.com/AboveColin/HA-Jev/releases)
[![License](https://img.shields.io/github/license/AboveColin/HA-Jev)](LICENSE)

# Jev for Home Assistant

Ask [TypeSafe Jev](https://typesafe.ai) questions about your house and get numbers
back. Jev is a decision model rather than a chat model, so it answers a typed
question with a probability, a choice or a score, and this integration turns each
answer into an entity you can automate on.

Not affiliated with TypeSafe. The API client is
[jevclient](https://github.com/AboveColin/jevclient).

## What it does

- Questions in `configuration.yaml` become sensors: a probability, one of your
  options with its distribution, or a number that can land between levels.
- Four actions answer inside an automation and return a response variable:
  `jev.noul`, `jev.choice`, `jev.score` and `jev.ask`.
- Point a question at entities, devices, areas, floors or labels in the normal
  picker and the state is built for you, so no template is needed.
- Reports what it spends: calls, input tokens and estimated cost per day, plus a
  daily token budget that halts evaluation when it trips.
- Thirteen worked [examples](examples/), four of them pairing Jev with an LLM.

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

## Installation

Requires Home Assistant 2026.9 or newer and an API key from
[typesafe.ai](https://typesafe.ai).

### HACS

Not in the HACS default list yet, so add it as a custom repository once.
[hacs/default#11052](https://github.com/hacs/default/pull/11052) is queued; when it
merges, steps 1 and 2 go away.

1. HACS, then the three dot menu, then **Custom repositories**.
2. Paste `https://github.com/AboveColin/HA-Jev`, set Type to **Integration**, **Add**.
3. Search HACS for **Jev**, then **Download**.
4. Restart Home Assistant.

[![Open your Home Assistant instance and open a repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=AboveColin&repository=HA-Jev&category=integration)

### Manual

Copy `custom_components/jev` from the
[latest release](https://github.com/AboveColin/HA-Jev/releases/latest) into your
`config/custom_components/` directory and restart. HACS will not update a copy
installed this way.

## Configuration

Settings, Devices and services, Add integration, then **Jev (TypeSafe)**. The API key
is the only thing it asks for, and it is checked before the entry is created.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=jev)

| Option | Where | Default | Description |
|---|---|---|---|
| API key | config flow | none | Your TypeSafe key |
| Daily input token budget | options | 0 | Stops evaluating for the day once spent. 0 means no limit |
| Price per million input tokens | options | 0.042 | Only affects the estimated cost sensor |

Use Reconfigure to replace the key later, which keeps your entities and history.

### Actions

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

All four take a template in `state`, or an object, or a list. They also take
`background:` for standing facts about how to read the state, which is
[worth more attached to the question than to the state](docs/measurements.md).

### Sensors

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

| Key | Required | Description |
|---|---|---|
| `name` | yes | Names the context and prefixes its entities |
| `entities` | one of these two | Entities, devices, areas, floors or labels to read |
| `state` | one of these two | Text or a template, alone or as a note beside the entities |
| `scan_interval` | no | Seconds between evaluations, minimum 30, default 300 |
| `trigger_entities` | no | Wake on these instead of on whatever `entities` names |
| `include_attributes` | no | Send every attribute of the picked entities, off by default |
| `questions` | yes | Each with `name`, `type`, `instructions`, and `criteria` for choice and score |

A context is one request, so keep related questions together. It is evaluated on
`scan_interval`, or when an entity it watches changes, debounced by 5 seconds. Adding
`threshold:` to a noul also creates a binary sensor to trigger on.

## Examples

| | |
|---|---|
| [01 laundry reminder](examples/01_laundry_reminder.yaml) | one question, one threshold, one binary sensor |
| [02 alert triage](examples/02_alert_triage.yaml) | three questions in one call, three notification paths |
| [03 doorbell triage](examples/03_doorbell_triage.yaml) | a choice on an intercom transcript |
| [04 situation layer](examples/04_situation_layer.yaml) | named situations other automations trigger on |
| [05 confidence gating](examples/05_confidence_gating.yaml) | act, ask, or stay quiet |
| [06 composite score](examples/06_composite_score.yaml) | several scores combined with your own weights |
| [07 Jev gates the LLM](examples/07_llm_jev_gate.yaml) | a cheap typed decision in front of an expensive call |
| [08 cascade](examples/08_llm_cascade.yaml) | low confidence escalates to a reasoning model |
| [09 guardrail](examples/09_llm_guardrail.yaml) | the LLM writes, Jev checks it against the source |
| [10 extract then verify](examples/10_llm_extract_verify.yaml) | the LLM pulls fields, Jev verifies each one |
| [11 post and parcels](examples/11_post_and_parcels.yaml) | one attention queue across several channels |
| [12 energy window](examples/12_energy_window.yaml) | where to keep arithmetic and where to ask |
| [13 voice commands](examples/13_voice_commands.yaml) | a command router, 12 questions per request |

The LLM examples use `ai_task.generate_data`, so they work with Google Generative AI,
OpenAI, Anthropic or a local Ollama. The voice command router follows TypeSafe's own
[smart home demo](https://docs.typesafe.ai/demos/smart-home) and builds its device
options from your entity registry, so the answer is an `entity_id` you can act on.

## Measurements

[docs/measurements.md](docs/measurements.md) has what was measured against the live
API: what an entity costs in tokens, why batching is nearly free, real latency from
Europe against the published figure, and the two findings that changed this code.

## Known limitations

- Answers carry no reasoning, so there is nothing to audit afterwards.
- Confidence has no published calibration evidence. Treat 0.9 as higher than 0.6
  until you have measured it on your own questions.
- Slower from Europe than the published 70 to 500 ms. Fine for a doorbell, too slow
  for a tight loop.
- Not for safety decisions. A probability with no explanation should not hold a lock,
  a heater or a smoke alarm.

## Troubleshooting

Turn on debug logging first. It prints every state sent, which is usually the answer:

```yaml
logger:
  logs:
    custom_components.jev: debug
```

| Symptom | Cause |
|---|---|
| An answer barely moves with the world | The state does not say what you assumed, or it holds a number the model is being asked to compare |
| Answers sit near 0.5 with low confidence | The question measures more than one thing. Split it |
| Entities unavailable, budget sensor on | The daily budget stopped evaluation |
| Entities unavailable, budget sensor off | Look for one line saying TypeSafe is not answering |
| Setup fails with "TypeSafe did not answer" | Connectivity, not configuration. Home Assistant retries |
| An error names a limit | It names your number too. 2 to 255 options, 2 to 10 levels, 250 entities |

## Contributing

Issues and pull requests welcome.

```bash
pip install -r requirements-test.txt
pytest
```

103 tests run the integration inside a real Home Assistant with the API client
replaced, so the suite spends nothing. `quality_scale.yaml` tracks this against Home
Assistant's quality scale, and `mypy --strict` runs in CI.

## Changelog

See the [release history](https://github.com/AboveColin/HA-Jev/releases).
