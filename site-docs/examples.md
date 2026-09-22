# Examples

Fifteen worked examples live in
[examples/](https://github.com/AboveColin/HA-Jev/tree/main/examples). Every one is
validated by the test suite, so they cannot rot silently.

| | |
|---|---|
| [01 laundry reminder](https://github.com/AboveColin/HA-Jev/blob/main/examples/01_laundry_reminder.yaml) | one question, one threshold, one binary sensor |
| [02 alert triage](https://github.com/AboveColin/HA-Jev/blob/main/examples/02_alert_triage.yaml) | three questions in one call, three notification paths |
| [03 doorbell triage](https://github.com/AboveColin/HA-Jev/blob/main/examples/03_doorbell_triage.yaml) | a choice on an intercom transcript |
| [04 situation layer](https://github.com/AboveColin/HA-Jev/blob/main/examples/04_situation_layer.yaml) | named situations other automations trigger on |
| [05 confidence gating](https://github.com/AboveColin/HA-Jev/blob/main/examples/05_confidence_gating.yaml) | act, ask, or stay quiet |
| [06 composite score](https://github.com/AboveColin/HA-Jev/blob/main/examples/06_composite_score.yaml) | several scores combined with your own weights |
| [07 Jev gates the LLM](https://github.com/AboveColin/HA-Jev/blob/main/examples/07_llm_jev_gate.yaml) | a cheap typed decision in front of an expensive call |
| [08 cascade](https://github.com/AboveColin/HA-Jev/blob/main/examples/08_llm_cascade.yaml) | low confidence escalates to a reasoning model |
| [09 guardrail](https://github.com/AboveColin/HA-Jev/blob/main/examples/09_llm_guardrail.yaml) | the LLM writes, Jev checks it against the source |
| [10 extract then verify](https://github.com/AboveColin/HA-Jev/blob/main/examples/10_llm_extract_verify.yaml) | the LLM pulls fields, Jev verifies each one |
| [11 post and parcels](https://github.com/AboveColin/HA-Jev/blob/main/examples/11_post_and_parcels.yaml) | one attention queue across several channels |
| [12 energy window](https://github.com/AboveColin/HA-Jev/blob/main/examples/12_energy_window.yaml) | where to keep arithmetic and where to ask |
| [13 voice commands](https://github.com/AboveColin/HA-Jev/blob/main/examples/13_voice_commands.yaml) | a command router, 12 questions per request |
| [14 conversation agent](https://github.com/AboveColin/HA-Jev/blob/main/examples/14_conversation_agent.yaml) | watching what the agent spends |
| [15 doorbell triage in the UI](https://github.com/AboveColin/HA-Jev/blob/main/examples/15_doorbell_triage_ui.yaml) | six questions, entity targets, three branches |

## Two worked answers

A washing machine that has finished but not been emptied. 1.4 W, door shut, 14
minutes since the programme ended:

![The laundry question answering 0.86](images/example-laundry.png)

A cable modem with one reading near its limit. SNR 31.2 dB against a healthy 33,
upstream power 50.4 dBmV against a 51 ceiling, 1,184 uncorrected errors:

![The connection question answering degraded](images/example-connection.png)

Read that second one closely. It answers `degraded` at 0.59 with `marginal` right
behind at 0.40, and a confidence of 0.46. That is the model saying the data is
genuinely ambiguous rather than pretending otherwise, and it is the best argument for
why the actions return confidence at all: an automation can require 0.8 before it
wakes anyone.

## Pairing with an LLM

Four of the examples combine Jev with `ai_task.generate_data`, Home Assistant's
provider-agnostic way to call a large language model. Set up Google Generative AI,
OpenAI, Anthropic or a local Ollama and point `entity_id` at what it creates.

The division of labour is the same each time. Jev decides, in about 300 ms for a
fraction of a cent, and returns a number your code branches on. The LLM writes prose
or handles what Jev is not sure about, and costs a hundred times more per call.
Putting the cheap typed decision in front of the expensive one is the whole point.

The voice command router in example 13 follows TypeSafe's own
[smart home demo](https://docs.typesafe.ai/demos/smart-home). It builds its device
options from your entity registry, so each answer is an `entity_id` you can act on.
