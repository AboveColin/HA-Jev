# The three answers

The API takes one **state** and a set of **questions**, and answers each one
independently. There are three kinds of question, and the integration uses the API's
own names for them so what you pick in the UI is what goes over the wire.

## Noul

A probability from 0 to 1. No confidence figure, because the number is the
confidence.

=== "In the UI"

    Pick **Noul**. Optionally set `true_means` and `false_means` to sharpen which way
    the answer points, and a **threshold** to also get a binary sensor.

=== "In YAML"

    ```yaml
    - name: Laundry forgotten
      type: noul
      instructions: Is the laundry finished but still sitting in the machine?
      "true": Finished, and nobody has taken it out
      "false": Still running, or already emptied
      threshold: 0.7
    ```

=== "As an action"

    ```yaml
    - action: jev.noul
      response_variable: laundry
      target:
        entity_id: sensor.washing_machine_power
      data:
        instructions: Is the laundry finished but still in the machine?
        threshold: 0.7
    ```

    Returns `noul` and `is_true`.

!!! warning "The key is spelled differently in YAML"
    A `jev:` block takes `"true":` and `"false":`, quoted, because unquoted YAML
    reads them as booleans and the schema never matches. The actions and the UI
    form take `true_means` and `false_means`. That inconsistency is historical and
    the docs say which is which rather than pretending otherwise.

## Choice

One of your options, with the whole distribution and a confidence.

**Between 2 and 255 options.** The integration checks that before it sends anything,
and the error names your number.

=== "In the UI"

    Pick **Choice**, then write your options one per line as `name: what it means`.
    The description is optional but usually earns its place.

=== "In YAML"

    ```yaml
    - name: Caller
      type: choice
      instructions: Who is at the door?
      criteria:
        delivery: A courier dropping off or collecting a parcel
        visitor: Someone the household knows
        sales: An unsolicited caller selling or canvassing
    ```

=== "As an action"

    ```yaml
    - action: jev.choice
      response_variable: caller
      data:
        instructions: Who is at the door?
        options:
          delivery: A courier dropping off a parcel
          visitor: Someone the household knows
    ```

    Returns `choice`, `probabilities` and `confidence`.

The sensor takes the winning option as its state, with the full distribution in its
attributes. Because the options are a fixed set, the sensor gets the `enum` device
class, so the UI knows what values are possible.

## Score

A rating against levels you describe, lowest first. The answer can land **between**
levels, which is the point: 2.4 out of "Not at all, Worth a glance, Look today, Right
now" says more than picking one.

**Between 2 and 10 levels.**

=== "In the UI"

    Pick **Score**, then write your levels one per line, lowest first.

=== "In YAML"

    ```yaml
    - name: Nudge urgency
      type: score
      instructions: How urgently should someone be reminded?
      criteria: [Not at all, When convenient, Soon, Right now]
    ```

=== "As an action"

    ```yaml
    - action: jev.score
      response_variable: urgency
      data:
        instructions: How urgently does this need a person?
        levels: [Never, Whenever, Within the hour, Right now]
    ```

    Returns `score`, `normalized`, `nearest_level`, `legend`, `probabilities` and
    `confidence`.

!!! tip "Order matters"
    Levels are read lowest first. Writing them in the wrong order does not error, it
    just gives you answers that are backwards.

## Picking between them

| You want | Use |
|---|---|
| A yes or no, or something to threshold | Noul |
| To route between a handful of named outcomes | Choice |
| A dial rather than a switch | Score |

When two options blur into each other, a Score with more levels often beats a Choice
with more options, because it can answer "between these two" instead of being forced
to pick.

## On confidence

Choice and Score return a confidence. Noul does not, because its number already is
one.

TypeSafe publishes no calibration evidence for confidence, and their own docs call it
a convenient default. Treat 0.9 as higher than 0.6 rather than as right nine times in
ten, until you have measured it on your own questions. See
[Limitations](limitations.md).
