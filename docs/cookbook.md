# Cookbook

Four patterns that use what this model is good at, and one that shows where to
stop.

The examples below use `jev.ask`, which asks several questions in one request. When
you only want one answer, `jev.noul`, `jev.choice` and `jev.score` are the same thing
with the question spelled out as form fields instead.

## 1. A situation layer

Home Assistant knows temperature, motion and power. It does not know that someone
is cooking, that a guest is staying over, or that nobody got up this morning. Every
automation that needs one of those rebuilds it from raw entities, which is why they
break when you move a sensor.

Ask once, in one context, and every situation becomes an entity.

```yaml
jev:
  - name: House
    scan_interval: 600
    state: >
      Time: {{ now().strftime('%H:%M on %A') }}.
      Motion in the last hour: {{ states.binary_sensor
         | selectattr('attributes.device_class', 'eq', 'motion')
         | selectattr('state', 'eq', 'on') | map(attribute='name') | join(', ') or 'none' }}.
      Kitchen power: {{ states('sensor.kitchen_power') }} W.
      Front door last opened: {{ relative_time(states.binary_sensor.front_door.last_changed) }} ago.
      Phones home: {{ states.device_tracker | selectattr('state','eq','home')
         | map(attribute='name') | join(', ') or 'none' }}.
    questions:
      - name: Cooking
        type: noul
        instructions: Is someone cooking right now?
        threshold: 0.7
      - name: Nobody home
        type: noul
        instructions: Is the house empty?
        threshold: 0.8
      - name: Guest present
        type: noul
        instructions: Is somebody staying who does not live here?
        threshold: 0.7
```

Three questions in one context is one request. Ten would be the same request, a few
hundred tokens larger and about as fast.

## 2. Alert triage

Most alerts are not worth a notification. Score them instead of routing them all.

```yaml
script:
  triage_alert:
    fields:
      text: {}
    sequence:
      - action: jev.ask
        response_variable: jev
        data:
          state: "{{ text }}"
          questions:
            real:
              type: noul
              instructions: Is this a real fault that needs a person, rather than a restart or a blip?
            urgency:
              type: score
              instructions: How urgently must someone act?
              criteria: [Ignore, This week, Today, Wake someone up]
      - choose:
          - conditions: "{{ jev.answers.urgency.score > 2.5 }}"
            sequence:
              - action: notify.mobile_app
                data: { message: "{{ text }}", data: { push: { interruption-level: critical } } }
          - conditions: "{{ jev.answers.real.noul > 0.6 }}"
            sequence:
              - action: notify.mobile_app
                data: { message: "{{ text }}" }
```

## 3. Act on the confident ones, ask about the rest

Confidence is a second axis. Use it to decide whether to act at all, not just what
to do.

```yaml
      - choose:
          - conditions: "{{ jev.answers.kind.confidence > 0.85 }}"
            sequence:
              - action: script.handle_automatically
          - conditions: "{{ jev.answers.kind.confidence > 0.5 }}"
            sequence:
              - action: notify.mobile_app
                data:
                  message: "Probably {{ jev.answers.kind.choice }}. Handle it?"
                  data: { actions: [{ action: "YES", title: "Yes" }] }
        default:
          - action: notify.mobile_app
            data: { message: "Not sure what this is. Have a look." }
```

Pick the thresholds by watching your own answers for a week. The numbers above are
a starting point, not a measurement, and TypeSafe publishes no calibration evidence
for the confidence value at all.

## 4. Read the whole distribution when the answer matters

A `score` entity gives you a number. Its attributes give you the shape behind that
number, and the shape carries information the number throws away.

```jinja
{% set p = state_attr('sensor.jev_nudge_urgency', 'probabilities') %}
{% if p['0'] > 0.3 and p['2'] > 0.3 %}
  The model is split between "not at all" and "right now", which usually means the
  state text is describing two different things. Narrow the context.
{% endif %}
```

A flat or two-humped distribution is a sign your question is doing two jobs. Split
it, and combine the answers in your own template, where you can see the arithmetic.

## 5. Where to stop

Do not put this in front of a lock, a heater, a smoke alarm or anything that costs
money to get wrong. The model returns a number with no reasoning attached, its
confidence value is not calibrated, and it answers in about 700 ms from Western
Europe. It is a good way to turn messy text into a signal. It is not a safety
device.
