# Conversation agent

Point Assist at Jev. **Settings**, **Voice assistants**, pick your pipeline, set
Conversation agent to **Jev**.

![Assist answering through Jev](images/assist.png)

## What it does

One sentence becomes one request carrying seven questions: what should happen, is it
compound, does it need text written, how is the target named, which entity, which
room, which kind of device.

Five or six of those answers are discarded on any given sentence. That is the cheap
shape, not waste: three questions measured 712 ms and a hundred measured 714, so
asking only the ones that turn out to matter would mean several round trips gated on
each other.

The options for "which device" come from your entity registry, so what comes back is
an `entity_id` that exists. The model picks from a list rather than writing one.

It runs Home Assistant's own intents: `HassTurnOn`, `HassTurnOff`, `HassToggle`,
`HassLightSet` and `HassGetState`. Media players, covers and climate setpoints go to
the fallback agent.

## What it refuses

| Case | What happens |
|---|---|
| Below the confidence floor | The whole sentence goes to the fallback agent, nothing done first |
| Two commands in one sentence | Fallback |
| Needs words written or looked up | Fallback |
| An entity you did not expose to Assist | Never described to the model at all |
| No room and no device named | Refused, unless you allow it. `turn_off` is exempt |

!!! info "It only sees what Assist sees"
    The agent describes only entities you exposed to Assist. You already decided
    which entities a voice assistant may touch, and a question is not a reason to
    widen that.

## What it costs

Every command counts against the same daily token budget as your sensors. A
satellite that mishears a wake word all night trips that tripwire instead of running
up a bill.

Measured live: 257 to 455 ms warm, 512 to 753 ms on the first call after a restart,
and 1,329 to 1,371 input tokens per command with five entities exposed. Thirty
commands came to $0.0017.

## Brightness comes from a regex

`set the lamp to 40 percent` has its number pulled out by pattern matching, not by
asking the model. Jev judges and does not calculate, and a regex is exact and free.

`40 percent`, `40%` and `40 procent` all work. `turn on 2 lamps` correctly yields no
brightness.

## A command that is already done

Three runs per starting state, one sentence, one entity:

| Desk lamp starts | Action confidence |
|---|---|
| off | 1.00, 1.00, 1.00 |
| on | 0.25, 0.28, 0.31 |

With the lamp already on, the sentence really could be a command or a question, and
the model says so by spreading its probability rather than moving the ranking:
`turn_on` stayed top at 0.39 to 0.48.

Reading only the winning answer called that unintelligible. The agent now compares
the top option against the current state and answers **"Desk lamp is already on"**.

## Options

| Option | Default | What it does |
|---|---|---|
| Fall back to this agent | none | Where unrouted sentences go. Empty means it says it did not understand |
| Act only above this confidence | 0.6 | Below it, the sentence goes to the fallback |
| Allow whole-house commands | off | Turning everything off is always allowed |

Point the fallback at Home Assistant's built-in agent and you get sentence matching
for what it already knows plus Jev for the rest. Point it at an LLM agent and the LLM
only sees what Jev could not route, which is the cheap arrangement.

## Diagnostics

The last 20 sentences the agent routed are in the integration's diagnostics, with the
reason for every decision and the action distribution behind it.

!!! warning
    Those are the sentences actually spoken in your house. Read the file before
    pasting it into a public issue.
