# Conversation agent

Point Assist at Jev. **Settings**, **Voice assistants**, pick your pipeline, set
Conversation agent to **Jev**.

![Assist answering through Jev](images/assist.png)

## What it does

One sentence becomes one request carrying five to seven questions. Five are always
there: what should happen, is it compound, does it need text written, how is the
target named, which entity. Which room is added when you have rooms holding exposed
entities, and which kind of device when the exposed entities span two domains or
more. A one-domain house with no areas is asked five.

All but one or two of those answers are discarded on any given sentence. That is the cheap
shape, not waste: three questions measured 712 ms and a hundred measured 714, so
asking only the ones that turn out to matter would mean several round trips gated on
each other.

The options for "which device" come from your entity registry, so what comes back is
an `entity_id` that exists. The model picks from a list rather than writing one.

It runs Home Assistant's own intents: `HassTurnOn`, `HassTurnOff`, `HassToggle`,
`HassLightSet` and `HassGetState`. Lights, switches, fans, covers, media players,
climate entities, vacuums, input booleans, scenes and scripts are turned on and off
this way. Climate setpoints, and anything else, go to the fallback agent.

Locks are not on that list and are never described to the model. Home Assistant reads
turn_on on a lock as `lock.lock` and turn_off as `lock.unlock`, the opposite way
round from how the command is spoken, and a probability with no reasoning should not
be deciding whether a door opens. Lock sentences go to the fallback agent. To
automate a lock with Jev, ask for it explicitly with `jev.choice` and call
`lock.lock` yourself, as [example 13](https://github.com/AboveColin/HA-Jev/blob/main/examples/13_voice_commands.yaml)
does.

Covers with the `garage`, `gate` or `door` device class are left out for the same
reason. Blinds, shades and curtains stay in.

A room command always carries the kinds of device the model was shown. Home
Assistant otherwise acts on every exposed entity in the room, so "turn off the
hallway" would reach a lock exposed there and unlock it.

## What it refuses

| Case | What happens |
|---|---|
| Below the confidence floor | The whole sentence goes to the fallback agent, nothing done first |
| Two commands in one sentence | Fallback |
| Needs words written or looked up | Fallback |
| A lock or a garage, gate or door cover | Fallback. The agent never describes one |
| An entity you did not expose to Assist | Never described to the model at all |
| No room and no device named | Refused, unless you allow it. `turn_off` is exempt. Below the confidence floor, fallback |
| Two kinds of device, no kind named, whole house | Asks which kind. With one kind exposed, it acts on that kind |
| Two devices whose names fit the command equally well | Asks which one, see below |
| Too little budget left for the command, a rejected key or no answer | Fallback. With no fallback agent it says which of the three it was, as an error reply |

!!! info "It only sees what Assist sees"
    The agent describes only entities you exposed to Assist. You already decided
    which entities a voice assistant may touch, and a question is not a reason to
    widen that. This is the voice path only. The [four actions](actions.md) send
    whatever an automation targets, exposed or not.

## When two devices fit the name

The agent compares what you said with the names of the exposed devices of the kind
the model picked. The whole name said wins: with a "Lamp" and a "Desk lamp", "turn on
the lamp" is the Lamp and "turn on the desk lamp" is the Desk lamp. When two names fit
equally well, as "the lamp" does for a Desk lamp and a Floor lamp, the agent asks
**"Do you mean Desk lamp or Floor lamp?"** and keeps the conversation open. When the
two have the same name, it adds the room: "Lamp (Office) or Lamp (Bedroom)". A room
you name settles it first, so "the lamp in the office" acts.

The names decide this, not the model's confidence. On a test instance with two lights
both called "Lamp", the model put 1.00 on one of them, every time, so a confidence
split never showed that there were two. With three or more that fit equally well, the
command goes to the fallback agent.

Your reply, such as "the desk one", is one more request, and it counts against the
budget. It asks which of the two the reply picks. A confident pick carries out the
first command on that device. A reply that picks neither is handled as a new command.
The question expires after five minutes, the same time Home Assistant keeps a
conversation open.

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

The percent word is read in every language the integration is translated into, so
`40 Prozent`, `40 pour cent`, `40 per cento`, `40 por ciento`, `40 procent`,
`40 процентов` and `百分之40` all give 40. A bare number needs a word about light
level next to it, `dimme ... auf 30` or `ztlum ... na 30`, or it stays a count.
With several numbers, the last one is the level: `dim bedroom 2 to 30` gives 30.

A relative change gives no brightness, so `20% brighter` and `dim it by 20` go to the
fallback agent rather than setting 20. A number over 100 or with a decimal point is
not a percentage.

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
| Fall back to this agent | none | Where unrouted sentences go. Empty means it says why it did nothing. Another Jev agent is refused, because two agents falling back to each other would pay for every pass |
| Act only above this confidence | 0.6 | Below it, the sentence goes to the fallback |
| Allow whole-house commands | off | Turning everything off is always allowed |

Point the fallback at Home Assistant's built-in agent and you get sentence matching
for what it already knows plus Jev for the rest. Point it at an LLM agent and the LLM
only sees what Jev could not route, which is the cheap arrangement.

## Diagnostics

In the Assist dialog, each reply from Jev has a note under it with what Jev answered:
the decision and its reason, the slots, each answer with its top three options, the
model, the input tokens and the time the call took. The pipeline's debug view
(**Settings**, **Voice assistants**, the pipeline's menu, **Debug**) keeps the same
note as an `intent-progress` event of the run. The note goes to the pipeline only. It
is not added to the conversation, so a fallback agent does not read it.

The last 20 decisions the agent made are in the integration's diagnostics, with the
reason for every decision and the action distribution behind it. The sentence itself
is redacted, because the file is meant to be pasted into an issue.
