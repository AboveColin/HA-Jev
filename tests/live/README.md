# Live tests

The file here is deliberately not named `test_*.py`. pytest imports what it collects,
and this script runs top to bottom on import, so a matching name would make a plain
`pytest` spend real tokens against a real instance. `pytest.ini` excludes the
directory as well.

`pytest` in the parent directory runs against a mocked client and spends nothing.
These do the opposite: they drive a real Home Assistant with a real API key, because
two things cannot be mocked honestly.

The first is the script engine. When an automation runs, Home Assistant renders the
templates in the action data before the action ever sees them, and the response
variable has to survive into the next step. A unit test calling the service directly
skips all of that.

The second is the model. Whether an answer actually tracks the world is not
something a mock can tell you.

## Running them

Point a throwaway Home Assistant at `fixture_configuration.yaml`, add the
integration through the UI, then:

```bash
python tests/live/run_live_automations.py
```

It expects the instance on `127.0.0.1:8124` with a user `dev`, which is what
`hactl.py` logs in as. Edit `hactl.BASE` for anywhere else.

## What it asserts, and what it deliberately does not

The third automation exists for one reason: an automation can pass its own
variables and its trigger data into the action, and the only way to prove each piece
arrived is to ask a question that is unanswerable without it. A mapping variable is
the interesting case, because it has to arrive as a mapping rather than as a
stringified dict. Turn on debug logging for `custom_components.jev` and the log
prints the type and the content of every state sent.

It asserts that all three automations write a result, that the token usage survives into
the automation, and that the answer **separates**: an idle machine and a running one
must differ by at least 0.3.

It does not assert that an idle machine reads above any particular number. Nobody
has published calibration evidence for this model, so a fixed level would be a
figure nobody can defend. What an automation actually needs is that the answer moves
with the world by enough to put a threshold between the two cases.

## The measurement that shaped the fixture

The fixture does its arithmetic in Jinja and hands the model a conclusion in words.
That is not decoration. Measured on the same question:

| What the state said | idle | running | separation |
|---|---|---|---|
| `1.2 W`, plus "this machine draws under 5 W when idle" | 0.39 | 0.33 | +0.06 |
| "drawing almost no power, which means it is idle" | 0.60 | 0.12 | +0.48 |

Inside the real automation the second form gives +0.50. The first form would make
this test fail, correctly.
