# Writing a question that works

The difference between a question that tracks the world and one that sits near 0.5
is usually not the model. It is what you told it.

## Tell it how to read the numbers

Jev makes a judgment. It does not compare numbers.

Hand it a reading of `1.2` and a threshold of `5` buried in prose and it will not
work out which side the reading falls on. Measured on the laundry question at 1.2 W
and at 1450 W, five runs per cell:

| What the state carried | idle | running | separation |
|---|---|---|---|
| the readings alone | 0.47 | 0.26 | +0.21 |
| the rule in `background:` | 0.70 | 0.10 | **+0.60** |
| the comparison done in the template | 0.74 | 0.06 | **+0.69** |
| both | 0.72 | 0.04 | +0.68 |

Either fix roughly triples the separation, and **they do not stack**, so do one.

=== "Put the rule in the question"

    No Jinja needed. This is the field labelled **Standing facts** in the UI.

    ```yaml
    background: >-
      This machine draws under 5 W when idle and over 300 W while a programme runs.
    ```

=== "Do the comparison yourself"

    Exact and free, and it hands over a conclusion in words.

    ```yaml
    state: >-
      The washing machine is
      {% if states('sensor.washing_machine_power') | float(0) < 5 %}
      drawing almost no power, which means it is idle
      {% else %}
      drawing {{ states('sensor.washing_machine_power') }} W, so a programme is running
      {% endif %}.
    ```

## Placement is most of the effect

The same sentence put in the **state** rather than in the **question** measured
+0.33, about half of what it is worth on the question.

So `background:` beats writing the same rule into your template.

## Ask one thing

A question that measures two things at once answers near 0.5 with low confidence,
because both halves are true some of the time.

!!! failure "Two questions wearing one coat"
    `Is the laundry finished and has nobody dealt with it?`

!!! success "One question"
    `Is the laundry finished but still sitting in the machine?`

If you need both, ask both. They go in the same request and cost almost no extra
time.

## A note beside the readings

A target on its own sends readings. Adding a note puts your text alongside them.

Asking whether the laundry was finished, about a power sensor and a door sensor,
returned **0.31** with the readings alone and **0.80** after one sentence saying the
programme had finished 14 minutes ago.

The model cannot know what your sensors do not say.

## Use the preview

Everything above is guesswork until you look. The
[preview](questions-ui.md#the-preview) shows the exact state and the answer it gets,
before the question exists. Change a word, look again.
