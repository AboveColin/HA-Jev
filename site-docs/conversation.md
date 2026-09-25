# Conversation agent

Point Assist at Jev. **Settings**, **Voice assistants**, pick your pipeline, set
Conversation agent to **Jev**.

![Assist answering through Jev](images/assist.png)

## What it does

One sentence becomes one request carrying nine to eleven questions. Nine are always
there: what should happen, is it compound, does it need text written, is it for
another time or on a condition, is it a position part of the way, does it leave
something out, does it say how bright, how is the target named, which entity. Which room is added when you have rooms holding exposed
entities, and which kind of device when the exposed entities span two domains or
more. A one-domain house with no areas is asked nine.

All but one or two of those answers are discarded on any given sentence. That is the cheap
shape, not waste: three questions measured 712 ms and a hundred measured 714, so
asking only the ones that turn out to matter would mean several round trips gated on
each other.

The options for "which device" come from your entity registry, so what comes back is
an `entity_id` that exists. The model picks from a list rather than writing one.

The aliases you give an entity in its voice settings go with it: "Lamp, in the
Office, also called Worktop". So "turn on the worktop" reaches the Lamp. On a test
house of twelve entities, seven with an alias, 3 of 8 alias sentences picked their
device below 0.6 without the aliases, and all 8 picked it at 0.91 or more with them.
The action question reads the aliases too: without them, "turn on the worktop" scored
its action 0.49 and went to the fallback. With them it scored 1.00. The aliases cost
about 15 input tokens each. An alias counts as a name when two devices fit what you
said, so a "Lamp" also called "Worktop lamp" wins "turn on the worktop lamp" over a
second "Lamp".

The aliases of an area go with it the same way: "Office, also called Snug, Study". On
a test house with four aliases on three areas, eight sentences run twice each, 9 of
16 found the room by its alias without the aliases, and "snug lights on" turned on
every light. With the aliases, 16 of 16 found it. They cost 75 input tokens in that
house, and nothing in a house whose areas have no aliases.

It runs Home Assistant's own intents: `HassTurnOn`, `HassTurnOff`, `HassToggle`,
`HassLightSet` and `HassGetState`. Lights, switches, fans, covers, media players,
climate entities, vacuums, input booleans, scenes and scripts are turned on and off
this way. Climate setpoints, and anything else, go to the fallback agent.

Playing, pausing, stopping and skipping media also go to the fallback agent. A
music player often has no `turn_off`, so "stop the music" read as turning it off
fails. With playback named as none of the above, a test of six playback sentences
all came back none of the above at 0.92 or more.

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

After an action it says the sentence Home Assistant's own agent says for the same
command, in the pipeline's language, such as "Turned on the light". Those sentences
come from Home Assistant's translations. Where they have none, as for a toggle, it
says "Done."

## What it refuses

| Case | What happens |
|---|---|
| Below the confidence floor | The whole sentence goes to the fallback agent, nothing done first |
| Two commands in one sentence | Fallback |
| Needs words written or looked up | Fallback |
| For another time, for a set time or on a condition, such as "turn off the lamp in 10 minutes" | Fallback. Home Assistant's intents have no timer, so the command would run now |
| A cover part of the way, such as "open the blinds halfway" | Fallback. `turn_on` opens a cover all the way |
| Something left out, such as "turn off everything but the TV" | Fallback. Home Assistant's intents cannot leave a device out, so the TV went off too |
| Nothing asked for, such as "I turned off the lamp" or "zet de lamp niet aan" | Fallback. Both acted before: the first turned the lamp off, the second turned it off instead of leaving it |
| A lock or a garage, gate or door cover | Fallback. The agent never describes one |
| An entity you did not expose to Assist | Never described to the model at all |
| A hidden device named in full, next to an exposed one with a shorter name | Fallback. "Turn on the desk lamp" does not turn on "Lamp" |
| No room and no device named | Refused, unless you allow it. `turn_off` is exempt. Below the confidence floor, fallback |
| Two kinds of device, no kind named, whole house | Asks which kind. With one kind exposed, it acts on that kind |
| Two devices whose names fit the command equally well | Asks which one, see below |
| The device cannot do the action, such as a player with no `turn_off` | Fallback. Home Assistant reports this only when no device changed |
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
you name settles it first, so "the lamp in the office" acts. Next comes the room of
the satellite that heard you, as for Home Assistant's own agent: "turn on the lamp"
said to the office satellite turns on the office Lamp.

The names decide this, not the model's confidence. On a test instance with two lights
both called "Lamp", the model put 1.00 on one of them in one session. In another it put
0.55 on "none of these", 0.44 on one Lamp and 0.01 on the other. Neither split showed
that there were two. With three or more that fit equally well, the command goes to the
fallback agent.

Your reply, such as "the desk one", is one more request, and it counts against the
budget. It asks which of the two the reply picks, and if the reply asks for something
of its own. A confident pick carries out the first command on that device. A reply
that picks neither, or that is a new instruction, is handled as a new command. So
"never mind, turn off the lamp in the bedroom" turns that lamp off, and does not run
the first command on it.
The question expires after five minutes, the same time Home Assistant keeps a
conversation open.

## What it costs

Every command counts against the same daily token budget as your sensors. A
satellite that mishears a wake word all night trips that tripwire instead of running
up a bill.

Measured live: 257 to 455 ms warm, 512 to 753 ms on the first call after a restart,
and 1,329 to 1,371 input tokens per command with five entities exposed. Thirty
commands came to $0.0017. That was with seven questions. The two added in 1.16.1
cost 127 more input tokens, 1,696 to 1,823 with twelve entities exposed, and the
same time warm: 261 ms before, 263 ms after. The question about something left out
and the option for nothing asked for, added in 1.17, cost 48 more: 1,743 to 1,751
before and 1,791 to 1,799 after, with twelve entities exposed. The question about
how bright cost 19 more: 1,733 before and 1,752 after, with eleven entities exposed.

In a test of 22 sentences run twice, 11 of them things to refuse, 14 runs acted when
they should not have before these two and 2 do now. Both are "turn off the lamps",
which turns off every light. In a control run of 77 sentences run twice, no sentence
that was right before is wrong now.

## Brightness

`set the lamp to 40 percent` has its number pulled out by pattern matching, not by
asking the model. Jev judges and does not calculate, and a regex is exact and free.
A level said in words has no digit to read, so the agent asks for it, see below.

`40 percent`, `40%` and `40 procent` all work. `turn on 2 lamps` correctly yields no
brightness.

The percent word is read in every language the integration is translated into, and
in Hungarian, so `40 Prozent`, `40 pour cent`, `40 per cento`, `40 por ciento`,
`40 procent`, `40 процентов`, `40 százalékra` and `百分之40` all give 40. A bare
number needs a word about light level next to it, `dimme ... auf 30` or `ztlum ... na 30`, or it stays a count.
With several numbers, the last one is the level: `dim bedroom 2 to 30` gives 30.

A relative change gives no brightness, so `20% brighter`, `dim it by 20` and
`20%-kal halványabbra` go to the fallback agent rather than setting 20. A word for
"to" in front of the number makes it a level, so `turn up the lamp to 80%`,
`verhoog de helderheid naar 80%` and `növeld a fényerőt 80%-ra` give 80. A word for
"by", or a word for changing with no "to", makes it an amount: `increase the
brightness by 20%`, `turn the lamp down 20%`, `erhöhe die Helligkeit um 20%` and
`把灯调亮20%` give none. In a test of 52 relative sentences in 14 languages, 45 set
the amount as the level before this rule and none do now. A number over 100 or with
a decimal point is not a percentage.

A sign in front of the number makes it an amount too, so `lamp +20%` and `lamp -20%`
give none. So do `bump`, `drop`, `fade`, `take 20% off`, `dimme`, `lager`, `omhoog`,
`dämpa`, `明るく` and `밝게` with no word for "to". A number on a scale of its own is
not a percentage: `3 out of 10`, `50/100`, `level 5` and `op stand 3` go to the
fallback agent rather than setting 10, 100, 5 and 3.

The agent reads the words for "to" in the pipeline's language. With the words of
every language at once, the Spanish `a` in `turn up the lamp a bit` counted as "to".

A digit in the name of a device, room or floor is not a level. `set lamp 2 brightness
to fifty percent` set 2% before, because the 2 was the only digit. Now the agent
removes the names first, so it reads no digit and asks for the level in words.

`turn on the lamp at 50%` sets 50. Home Assistant's `HassTurnOn` has no level, so
before this the lamp came on at its last level. Only a number with a percent counts
here, because `tänd 2 ljus` and `включи 2 свет` hold a word for light that is also a
word for brightness.

`switch on the lamp at half brightness` and `turn on the lamp at full brightness` set
50 and 100. A question in the first request asks if the command says how bright the
light should be, and a turn_on with a yes goes on as a level said in words, see
below. Before this the lamp came on at its last level. `turn on the desk lamp dimmed`
names no level the second request can read, so it goes to the fallback agent. In two
runs each, the question put 0.60 to 0.96 on six commands with a level and 0.01 to
0.02 on seven with none. The action alone put 0.04 to 0.39 on set_brightness for the
six, too little to act on.

In a test of 70 sentences with a digit, 24 read differently from the intended level
before these rules and 3 do now. Two are speech-to-text forms, `forty 5 percent` and
`4 0 percent`. The third, `set the lamp to 40% at 7`, goes to the fallback agent.

### A level said in words

`set the lamp to forty percent`, `zet de lamp op zestig procent`, `half brightness`
and `full brightness` have no digit in them. For such a sentence, and only for it,
the agent sends one more request with two questions: which level, from 10% to 100%
in steps of ten, and does the sentence change the brightness by an amount rather
than name a level. A word gives a whole ten, so `a quarter` sets 20.

It acts only when all three of these agree. The level question is sure, at 0.8 or
the agent's own floor if that is higher. The amount question says it is a level. The
sentence has no word for changing with no word for "to", the same words the regex
uses. Only those words refused `把灯调亮百分之二十`, which the model read as a level at
0.84 and 0.90. Only the floor refused a run that read `тридцать процентов` as 40.

In four runs of 20 levels and 22 amounts in words, in 10 languages, 17 levels were
set right each time and 3 went to the fallback, and no amount was set as a level.

The level options start at 10%, so `zero percent` and `nul procent` set 10 in 6 runs
of 6. A word for zero now sets 0, but only when the model also picked the lowest
level. When it picked another level, the sentence goes to the fallback agent.
Before this, all 20 levels went to the fallback. The second request costs 400 to 565
input tokens and 220 to 646 ms. A sentence with a digit in it never sends it.

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
