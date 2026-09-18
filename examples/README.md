# Examples

Copy-paste YAML for `configuration.yaml` and the automation editor. Every file is
self-contained and uses invented entity ids, so change those and nothing else.

| File | What it shows |
|---|---|
| [01_laundry_reminder.yaml](01_laundry_reminder.yaml) | the smallest useful thing: one noul, a threshold, a binary sensor |
| [02_alert_triage.yaml](02_alert_triage.yaml) | one call, three questions, three different notification paths |
| [03_doorbell_triage.yaml](03_doorbell_triage.yaml) | a choice on an intercom transcript, acted on in 300 ms |
| [04_situation_layer.yaml](04_situation_layer.yaml) | a layer of named situations every other automation can trigger on |
| [05_confidence_gating.yaml](05_confidence_gating.yaml) | act, ask, or stay quiet, decided by confidence |
| [06_composite_score.yaml](06_composite_score.yaml) | several scores combined with weights you own |
| [07_llm_jev_gate.yaml](07_llm_jev_gate.yaml) | Jev decides whether an LLM call is worth making |
| [08_llm_cascade.yaml](08_llm_cascade.yaml) | Jev answers the easy ones, an LLM gets the rest |
| [09_llm_guardrail.yaml](09_llm_guardrail.yaml) | an LLM writes, Jev checks it before it is sent |
| [10_llm_extract_verify.yaml](10_llm_extract_verify.yaml) | an LLM extracts fields, Jev verifies each against the source |
| [11_post_and_parcels.yaml](11_post_and_parcels.yaml) | one attention queue across several channels |
| [12_energy_window.yaml](12_energy_window.yaml) | where to keep the arithmetic and where to ask |
| [13_voice_commands.yaml](13_voice_commands.yaml) | a voice command router: 12 questions in one request, most of them thrown away |
| [14_conversation_agent.yaml](14_conversation_agent.yaml) | the built-in conversation agent: what to watch, and routing text that never reached Assist |

## Three rules that change the answers

Everything here follows them, and files 01 and 12 show the difference measured.

**Do the arithmetic yourself, or state the rule in `background:`.** Jev makes a
judgment, it does not compare numbers. Handing it `1.2` and a threshold of `5` and
expecting it to work out which side it falls on does not work: measured, that
separated an idle washing machine from a running one by 0.06. Putting the rule in
`background:` gave 0.60 and doing the comparison in Jinja gave 0.69. They do not
stack, so do one.

**One dimension per question.** A question that measures three things at once
produces a low confidence and a meaningless middle. Split it and combine the answers
in your own template, which is what 06 does.

**Ask everything at once.** Every question in a call is judged independently against
the same state, in parallel. Three questions took 712 ms and a hundred took 714 ms.
Adding a question costs tokens, not time, so 02 asks three where most people would
make three calls.

## The voice command router

[13_voice_commands.yaml](13_voice_commands.yaml) is the biggest one and follows
TypeSafe's own [smart home demo](https://docs.typesafe.ai/demos/smart-home). It asks
twelve questions in a single request and reads three of them, which is the whole
argument for speculative fan-out: the alternative is three round trips gated on each
other.

It differs from the demo in one way. The device and room options are built from your
own entity registry, so the answer to "which device" is a real `entity_id` with no
mapping table to keep in step.

Three things in it came out of watching it fail on a real instance:

Confidence decides which answer to trust. On "turn on the kitchen lights" the scope
answer was `one_room` at 0.41 while the device answer was `light.kitchen_lights` at
1.00. Branching on scope first threw away the certain answer for the uncertain one.

It acts on one entity per call. A single call carrying the whole list fails as a
whole the moment one entity refuses, so "shut off all the music" left everything
playing because one player does not support `turn_off`. Per entity, that command now
stops six of eight speakers and skips the two that genuinely cannot be stopped.

It never stops silently. A bare `condition:` inside a branch ends the script with no
trace, which is the worst way for an automation to do nothing, so it logs what it
understood and why it did not act.

## The LLM examples

Four of these combine Jev with `ai_task.generate_data`, which is Home Assistant's
provider-agnostic way to call a large language model. Set up Google Generative AI,
OpenAI, Anthropic or a local Ollama, and point `entity_id` at the AI task entity it
gives you.

The division of labour is the same every time. Jev decides, in about 300 ms for a
fraction of a cent, and returns a number your code can branch on. The LLM writes
prose, or handles the cases Jev is not sure about, and costs a hundred times more
per call. Putting the cheap typed decision in front of the expensive one is the
whole point.
