# Contributing

Bug reports, measurements and blueprints are all welcome. The bar for a pull
request is the same as for the maintainer: the full check suite passes, and every
number in the change says where it came from.

```sh
pip install -r requirements-test.txt
ruff check . && ruff format --check . && mypy --strict custom_components/jev && pytest
```

`tests/live` spends real tokens against a real instance and is not part of the
repository. Nothing you run here calls TypeSafe: the tests replace the client.

## Share a blueprint

A blueprint is the easiest thing to contribute, and the most useful one. If you
have an automation that asks Jev something, other people have the same house.

1. Put it in `blueprints/automation/jev/<what_it_does>.yaml`.
2. Add a case for it in `tests/blueprints/` (see below).
3. Add a row to `site-docs/blueprints.md`.
4. Open a pull request. Say what you run it on at home and what Jev answered, if
   you have seen it answer. A real answer is worth more than a description.

Put your GitHub name in `author:`. The blueprint page credits you.

### What makes a good Jev blueprint

**Ask only what a template cannot decide.** "Is the power under 5 W?" is a
comparison, and Jinja does it exactly and for free. "Is the laundry done and
forgotten?" is a judgment over the power, the door and the time, and that is the
question for Jev. When a rule decides part of it, write the rule in `background:`
or do the comparison in a template before the call. Measured on a washing machine,
the rule in `background:` moved the answer 0.60 and leaving it out moved it 0.06.

**Every call costs the user money.** A blueprint decides how often it runs, so it
decides the bill.

- A state trigger has a `for:` hold, taken from an input with a default of a minute
  or more.
- `mode: single` and `max_exceeded: silent`, and a cooldown input that ends the run
  with a `delay:`, so a flapping sensor asks once and not fifty times.
- A state trigger with no `to:` also fires when only an attribute changes. Give it
  `not_to: [unavailable, unknown]`. It then asks on real state changes only, and
  not when a sensor drops out.
- An unavailable sensor is not 0. Use `float(none)` and tell Jev the value is
  unknown, or do not ask.
- A time trigger runs a few times a day at most. The description says how often
  the blueprint asks at worst with the default inputs, as "N calls a day". The
  test suite checks for that number.

**Let the integration describe the house.** Pass entities as
`target: entity_id: !input ...`. The action sends each one's name, state, unit,
area and how long ago it changed, and the time. Use `state:` for text that is not
an entity, such as a transcript or a message, and for a verdict a template worked
out.

**One thing per question.** A question about three things gets a low confidence
and a meaningless middle. Ask three questions in one `jev.ask` call: they cost
tokens, not time.

**The user owns the actions.** Take what happens next as `selector: action: {}`
inputs, with a default of `[]` when the branch is optional. Do not call a notify
service by name. Set variables the actions can read, and list them in the
description: "Your actions can read `probability` and `choice`."

**A blueprint about one device may operate it.** A fan speed or an EV charger is
the point of that blueprint, so it may call that device's own service directly.
Its case must assert the call and its data. Anything else goes through the user's
actions.

**Never open the house.** No lock, alarm, garage door or gate. The conversation
agent refuses them for the same reason: a probability with no reasoning does not
decide whether a door opens.

**Use invented entity ids.** `sensor.washing_machine_power`, not your own. Room
names, addresses and hostnames stay out.

### Metadata

```yaml
blueprint:
  name: Jev - <What it does, in sentence case>
  description: >-
    What it asks, when, about N calls a day, and the variables your actions
    can read.
  domain: automation
  author: <your GitHub name>
  homeassistant:
    min_version: 2026.9.0
  source_url: https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/<file>.yaml
```

The test suite checks the name prefix, the `source_url` and the minimum version.

### The test case

`tests/test_blueprints.py` creates an automation from each blueprint in a test
Home Assistant, fires its real trigger, answers with a mocked Jev and checks which
of your actions ran. Add your case to the group file in `tests/blueprints/` that
fits, or start a new one and add it to `tests/blueprints/__init__.py`.

```python
"yes_no_question": Case(
    states={"sensor.washer_power": ("1.2", {"unit_of_measurement": "W"})},
    inputs={
        "entities": ["sensor.washer_power"],
        "hold": NO_WAIT,
        "cooldown": NO_WAIT,
        "question": "Is the laundry finished but still in the machine?",
        "yes_actions": run("yes", p="{{ probability }}"),
        "no_actions": run("no", p="{{ probability }}"),
    },
    fire=change("sensor.washer_power", "0.8"),
    answers={"answer": NoulAnswer(noul=0.83)},
    expect={"yes": [{"p": 0.83}]},
),
```

Your actions call `test.yes`, `test.no` or `test.other`, and put the variables your
description promises into the call's data. `expect` lists what each one received.
Set holds and cooldowns to `NO_WAIT`. `fire` is `change(...)`, `at(hour)` or
`event(...)` from `tests/blueprints/kit.py`. The single-question actions answer
under the key `answer`. `jev.ask` answers under your own question keys.

Check that the case goes red: misspell a variable in the blueprint and run
`pytest tests/test_blueprints.py -k <name>`. It must fail.

## Style

Plain English, short sentences, active voice. No em dashes. Say what a thing does
or give the number.
