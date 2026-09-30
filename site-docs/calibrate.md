# Tuning a threshold

A noul is a probability, and the threshold that turns it into yes or no is your
choice. 0.5 is where the model says it cannot tell. It is not where your washing
machine is done. `jev.calibrate` finds a threshold from what really happened in your
house.

## What it needs

- a probability sensor, such as a Jev noul sensor
- an entity that shows what was really true, such as a door contact, or a smart plug
  that reports when a machine runs
- the recorder, which keeps the history of both

It reads the recorder and nothing else. It does not call TypeSafe, so it costs no
tokens.

```yaml
- action: jev.calibrate
  response_variable: fit
  data:
    entity_id: sensor.jev_laundry_done
    truth_entity_id: binary_sensor.laundry_door
    truth_state: "off"
    days: 7
```

| Field | Default | Meaning |
|---|---|---|
| `entity_id` | required | The probability sensor |
| `truth_entity_id` | required | The entity that shows what was true |
| `truth_state` | `on` | The state of that entity that means yes |
| `days` | 7 | How far back to read. The recorder keeps 10 days unless you changed `purge_keep_days` |

## What it returns

```yaml
threshold: 0.31
precision: 1.0
recall: 1.0
f1: 1.0
hours: 4.0
hours_true: 2.0
times_true: 1
probability_changes: 4
table:
  - {threshold: 0.1, precision: 0.5, recall: 1.0, f1: 0.667}
  # ... one row for each tenth up to 0.9
error_budget:
  - {error_rate: 0.01, yes_at: 0.21, no_below: 0.21, automated: 1.0, yes_wrong: 0.0, no_wrong: 0.0}
  - {error_rate: 0.05, yes_at: 0.21, no_below: 0.21, automated: 1.0, yes_wrong: 0.0, no_wrong: 0.0}
```

The action cuts the window at every change of either entity, and counts time, not
state changes:

- **precision** is the part of the time the threshold said yes that was really true.
  A low precision means false alarms.
- **recall** is the part of the true time where the threshold said yes. A low recall
  means missed cases.
- **f1** balances the two. The action tries every hundredth from 0.01 to 0.99 and
  returns the one with the highest f1. When several tie, it returns the middle one, so
  a probability a little off its usual values still lands on the same side.

Use `table` when you care more about one side. For a notification you would rather
miss than repeat, pick a row with higher precision.

## How much can run alone

One threshold makes every answer a yes or a no, and some of those answers are
wrong. `error_budget` answers a different question. If a wrong answer may happen
only once in 100 (`error_rate: 0.01`) or once in 20 (`0.05`), how much of the time
can the noul decide without you?

- **yes_at** is the lowest threshold at which a yes was wrong no more often than
  the rate allows. At or above it, act.
- **no_below** is the highest threshold at which a no was wrong no more often than
  the rate allows. Below it, do nothing.
- **automated** is the part of the time the probability was outside the band
  between the two. In the band, ask a person.
- **yes_wrong** and **no_wrong** are how often each side was really wrong in the
  window. They are at or below the rate.

A side is `null` when no threshold kept it inside the rate. Then `automated`
counts only the other side. A noul that stays near 0.5 automates nothing, and
that is the correct result: the model cannot tell, so you have to decide.

The [Act when sure, ask when unsure](blueprints.md) blueprint uses the same band.
Put `yes_at` in its high threshold and `no_below` in its low threshold. Its
sliders move in steps of 0.05, so round `yes_at` up and `no_below` down. Rounding
the other way lets more wrong answers through than the rate allows. The high
slider starts at 0.5 and the low slider stops at 0.5.

A noul that separates the two cases perfectly, as in the example, gets one
threshold for both sides and `automated: 1.0`. The rates and the counts come from
the same window as `f1`, so read `times_true` before you trust them too.

## How much to trust it

Read `times_true` before you read `f1`. One wash is one occasion, and an f1 of 1.0 over
one occasion tells you almost nothing. A week with a machine that runs every other day
gives three or four. Collect more occasions before you move a threshold far.

Time when the probability sensor has no number, for example while the API was down,
is left out.

## When it refuses

| Error | Why |
|---|---|
| The recorder is not running | The action has nothing to read |
| No history | No time in the window has both a number and a truth state |
| Never true, or always true | With only one side, every threshold gets the same score |
