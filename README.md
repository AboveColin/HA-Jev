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

HACS, as a custom repository, until the integration is in the default list. Add
`https://github.com/AboveColin/HA-Jev` as an Integration, install, restart, then add
**Jev (TypeSafe)** from Settings, Devices and services. It asks for an API key and
checks it by asking one short question.

## Ask questions

Questions live in `configuration.yaml`, grouped into contexts. A context is one
piece of text plus everything you want to know about it.

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
took 712 ms and a hundred took 714 ms. So put related questions in one context and
they cost almost nothing extra in time.

They do cost money. Question text is billed as input, roughly 38 tokens for a short
one, so a hundred questions is a few thousand tokens per evaluation rather than a
few hundred.

## Ask from a script

The `jev.ask` action answers in the same script, for anything that does not deserve
a permanent entity.

```yaml
script:
  triage_doorbell:
    sequence:
      - action: jev.ask
        response_variable: jev
        data:
          state: "{{ trigger.payload }}"
          questions:
            sales:
              type: noul
              instructions: Is this someone selling something door to door?
            kind:
              type: choice
              instructions: What kind of caller is this?
              criteria:
                delivery: A parcel or food delivery
                neighbour: Somebody who lives nearby
                sales: Selling energy, internet or charity subscriptions
      - if: "{{ jev.answers.sales.noul > 0.8 }}"
        then:
          - action: media_player.play_media
            target: { entity_id: media_player.intercom }
            data: { media_content_id: "/local/nee_dank_u.mp3", media_content_type: music }
```

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

## What this will not do

It does not explain itself. An answer is a number, with no reasoning attached, so
anything you need to audit later needs its evidence recorded elsewhere.

Confidence is not calibrated. TypeSafe publishes no calibration evidence, and its own
docs call the value a convenient default. Treat 0.9 as "higher than 0.6", not as
"right 90 percent of the time", until you have measured it on your own questions.

It answers in about 700 ms from Western Europe, not the 70 to 500 ms TypeSafe
publishes for their own region. Fine for a doorbell, too slow for anything in a
tight loop.

Do not put it in front of a safety decision. A probability with no explanation is not
the right thing to hold a lock, a heater or a smoke alarm.

## Not affiliated with TypeSafe

Independent integration. The API client is [jevclient](https://github.com/AboveColin/jevclient).
