# Jev and the Bayesian sensor

Home Assistant already has a sensor that turns several readings into one probability,
the [Bayesian binary sensor](https://www.home-assistant.io/integrations/bayesian/).
Both give you a number from 0 to 1 and a threshold that turns it into on or off. They
get the number in different ways, and that decides which one fits.

## How each gets its number

The Bayesian sensor multiplies probabilities you give it. You set a prior, and for
each observation you say how often it is seen when the answer is yes
(`prob_given_true`) and when it is no (`prob_given_false`). An observation is a state,
a numeric range or a template. The sensor combines them with Bayes' rule and treats
them as independent of each other.

Jev reads the states and your question in words, and the model gives the probability.
You write no probabilities. You write what the question means, and optionally what
counts as yes and as no.

## Side by side

| | Bayesian sensor | Jev noul |
|---|---|---|
| You supply | a prior and two probabilities per observation | a question in words |
| Where it runs | in Home Assistant | a call to the TypeSafe API |
| Cost | nothing | input tokens, see [what it costs](cost.md) |
| Time to update | when a state changes | 250 to 580 ms warm, measured from the Netherlands |
| Same input, same output | yes | no. One sentence gave 0.25, 0.28 and 0.31 in three runs |
| Reads free text | only through a template you write | yes, a note or a transcript is ordinary input |
| Observations that depend on each other | counted as independent, so they count twice | read together |
| Why it said what it said | each observation's share is visible | a number with no reasoning |

## Which to use

Use the Bayesian sensor when you know, or can measure, how often each reading goes with
the answer, and the readings are few and mostly independent. "Somebody is home" from a
phone, a door and motion in the hall is the textbook case. It costs nothing, it works
without the internet, and you can see why it changed.

Use Jev when the probabilities are the part you cannot write down. A washing machine
whose power draw rises and falls through a programme, a doorbell transcript, or three
readings that only mean something together are hard to express as
`prob_given_true`. Jev takes them as they are.

If you already have a working Bayesian sensor, keep it. Jev does not make one wrong.

## Using both

A Jev noul sensor is a number, so a Bayesian sensor can use it as a `numeric_state`
observation, next to readings that it handles well on its own:

```yaml
binary_sensor:
  - platform: bayesian
    name: Laundry waiting
    prior: 0.2
    probability_threshold: 0.8
    observations:
      - platform: numeric_state
        entity_id: sensor.jev_laundry_done
        above: 0.6
        prob_given_true: 0.9
        prob_given_false: 0.1
      - platform: state
        entity_id: binary_sensor.laundry_door
        to_state: "off"
        prob_given_true: 0.95
        prob_given_false: 0.5
```

The probabilities in that example are placeholders. Measure your own, for example with
[jev.calibrate](calibrate.md), which reports precision and recall for a Jev sensor
against an entity that shows what was really true.
