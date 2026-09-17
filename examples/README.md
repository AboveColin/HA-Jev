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
