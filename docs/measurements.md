# Measurements

Everything here was measured against the live API, mostly from a consumer connection
in the Netherlands. Repeated runs of the same cell wander by around 0.15, so treat
the gaps as the finding rather than the digits.

## Tell it how to read the numbers

Jev makes a judgment and does not compare numbers. Hand it a reading with the rule
buried in surrounding prose and it will not work out which side of the threshold the
reading falls on.

Asking whether the laundry is finished but still in the machine, at 1.2 W and at
1450 W, five runs per cell:

| What the state carried | idle | running | separation |
|---|---|---|---|
| the readings alone | 0.47 | 0.26 | +0.21 |
| the rule in `background:` | 0.70 | 0.10 | +0.60 |
| the comparison done in the template | 0.74 | 0.06 | +0.69 |
| both | 0.72 | 0.04 | +0.68 |

Either fix roughly triples the separation and they do not stack, so do one.

Placement is most of the effect. The same sentence put in the state rather than the
question measured +0.33, about half of what it is worth on the question.

The `background:` field needs no Jinja:

```yaml
    background: >-
      This machine draws under 5 W when idle and over 300 W while a programme runs.
```

The other route does the comparison in Jinja, where it is exact and free, and hands
over the conclusion in words:

```yaml
state: >-
  The washing machine is
  {% if states('sensor.washing_machine_power') | float(0) < 5 %}
  drawing almost no power, which means it is idle
  {% else %}
  drawing {{ states('sensor.washing_machine_power') }} W, so a programme is running
  {% endif %}.
```

## A note beside the readings

A target on its own sends readings. Adding `state:` puts your text alongside them as
a `note`. Asking whether the laundry was finished, about a power sensor and a door
sensor, returned 0.31 with the readings alone and 0.80 after one sentence saying the
programme had finished 14 minutes ago.

## What an entity costs

One entity made a 339 token request, five made 559, ten made 931, so an entity record
is 65.8 input tokens. The 250 entity cap on a target is therefore about 16,500 tokens
or $0.0007 per evaluation.

`include_attributes` sends every attribute as well. A weather forecast runs to
thousands of tokens on every evaluation, which is why it is off by default.

## Batching

Three questions took 712 ms and a hundred took 714, a difference of 24 ms for 97 more
questions. Four hundred questions took 1.3 s. Adding a question costs tokens, not
time.

Question text is billed as input at roughly 38 tokens for a short one, so a hundred
questions is a few thousand tokens per evaluation rather than a few hundred.

## Latency

TypeSafe publishes 70 to 500 ms. Measured across 16 calls from the Netherlands, a
warm connection answers in 250 to 580 ms and the first call after an idle spell takes
700 to 900 ms. Their figures were measured near their own service.

## Structured criteria, where I found nothing

`instructions` and every criteria value accept an object or an array, and the docs
say structure sharpens the boundary when two options blur. On five deliberately
ambiguous doorbell callers, three runs each:

| | agreed with the intended answer | mean confidence | unstable |
|---|---|---|---|
| flat strings | 12/15 | 0.90 | 0/5 |
| structured `what`/`not_for`/`examples` | 12/15 | 0.87 | 0/5 |

An easier set gave 12 of 12 for both. TypeSafe's own examples show modest gains on
some inputs and none on others. It is supported, and worth reaching for only when two
options genuinely blur and a plain sentence has already failed.

## Confidence decides which answer to trust

From building the voice command router. On "turn on the kitchen lights" the scope
answer came back `one_room` at 0.41 while the device answer came back
`light.kitchen_lights` at 1.00. Branching on scope first threw away the certain
answer in favour of the uncertain one and turned on every light in the house.

Confidence itself has no published calibration evidence, and TypeSafe's own docs call
it a convenient default. Treat 0.9 as higher than 0.6 rather than as right nine times
in ten, until you have measured it on your own questions.
