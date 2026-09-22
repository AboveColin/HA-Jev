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

Three questions took 712 ms and a hundred took 714, so 97 more questions cost 2 ms. Four hundred questions took 1.3 s. Adding a question costs tokens, not
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

## What one spoken command carries

Measured locally on the payload the conversation agent builds, with no API call, so
these are sizes rather than tokens:

| exposed entities | questions | state bytes | question bytes | total |
|---|---|---|---|---|
| 5 | 7 | 714 | 2,428 | 3,142 |
| 10 | 7 | 1,267 | 2,791 | 4,058 |
| 20 | 7 | 2,403 | 3,547 | 5,950 |
| 50 | 7 | 5,787 | 5,791 | 11,578 |
| 150 | 7 | 17,187 | 13,391 | 30,578 |

The question count does not move, which is the point: every question the router could
need goes in one request. An entity adds 114 bytes to the state and 76 to the options,
because it appears once as a reading and once as something to choose between.

Against the 65.8 input tokens per state-only entity record measured above, that scales
to roughly 110 input tokens per entity per command. A house with 20 entities exposed to
Assist is then about 2,200 tokens, and the 150 entity cap is about 16,500, or $0.0007
at the published price. These are derived from a measured figure, not measured
end to end: nobody has run a token count against the live API for this payload yet.

## Where the numbers are read, and where they are asked for

Jev judges and does not calculate, which is why "set the lamp to 40 percent" has its
number pulled out by a regex rather than by a question. The same finding that gave
0.06 separation on a raw threshold and 0.69 on a pre-computed comparison applies here.
A regex is exact, free, and cannot be wrong about what 40 means.

## Live on a real instance

Measured against a real Home Assistant with a real API key, five exposed entities in
three rooms, all of them in-memory fixtures.

Sixteen sentences, one request each: 257 to 455 ms warm, 512 to 753 ms on the first
call after a restart. That matches the 250 to 580 ms warm figure measured from the
same country earlier.

Input tokens sat between 1,329 and 1,371 per command, against 1,365 for the shortest
sentence in the set. So with a small house the seven questions dominate and the
sentence itself is noise. Thirty commands cost $0.0017 in total, or $0.000057 each.

That corrects the estimate derived from the per-entity figure. Entity records scale at
about 110 tokens each, but the fixed question text is roughly 1,300 tokens, so a house
with 5 exposed entities pays mostly for the questions and one with 150 pays mostly for
the entities.

## Bytes per input token

The daily budget has to refuse a call before it is sent, and the only token count
there is comes back with the reply. So the size of the request is measured locally and
divided by a bytes-per-token ratio.

The cold-start ratio comes from the two readings above. The conversation payload with
5 exposed entities and 7 questions is 3,142 bytes, and the same shape against the live
API reported 1,329 to 1,371 input tokens per command:

| | |
|---|---|
| 3,142 / 1,371 | 2.29 bytes per token |
| 3,142 / 1,329 | 2.36 bytes per token |

2.29 is the seed, because the lower ratio is the larger token estimate and an estimate
that refuses slightly early beats one that lets a call through.

This is a derivation across two runs rather than one payload counted both ways: the
byte figures were measured locally with no API call, and the token figures came from a
different set of sixteen live commands on the same fixtures. That is exactly why the
ratio is a seed and not a constant. Every answered call replaces it with its own
payload bytes divided by the input tokens the endpoint reported, so a different
tokenizer, a gateway, or OpenRouter is measured rather than assumed, and the assumption
lasts one call.

The estimate carries a 1.2 margin. Input tokens across those sixteen commands varied by
3.2%, 1,329 to 1,371 for the same seven questions, so 20% sits well past anything
measured. A budget you can trip by 4% of drift is a budget that goes off for no reason.

## A command that is already done reads as a low-confidence one

Three runs per starting state, one sentence, one entity, nothing else changed:

| desk lamp starts | action confidence |
|---|---|
| off | 1.00, 1.00, 1.00 |
| on | 0.25, 0.28, 0.31 |

The distribution with the lamp already on stayed ranked the same way, at turn_on 0.39
to 0.48, get_state 0.30 to 0.35, none_of_these 0.22 to 0.30. With the lamp on, the
sentence really could be either a command or a question, and the model says so by
spreading the probability rather than by moving the ranking.

Reading only the winning answer made a redundant command look unintelligible. Reading
the top option and comparing it against the current state answers "Desk lamp is
already on" instead.

## Two failures a unit test would not have found

The area options were built from every area in the registry, including rooms holding
nothing exposed. On a real instance "kill the lights in the kitchen" came back as that
room at 0.98, which was the right answer to the question asked and named somewhere the
agent could not act. The rooms offered are now only those holding an exposed entity.

A whole-house command sent no target at all. Home Assistant requires one of name, area
or floor, so "turn everything off" answered "Sorry, that did not work" with the model
right at 0.99. It now sends the literal name "all", which Home Assistant reads as every
entity, and it needs a domain beside it: a bare "all" is refused with "Service handler
cannot target all devices", so a whole-house command with no kind of device now asks
which kind.
