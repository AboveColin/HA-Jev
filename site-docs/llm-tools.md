# Tools for other LLM agents

An LLM conversation agent can read your house through the Assist API. What it cannot
give is a probability. Ask it whether the washing machine is done, and it says yes or
no. With this option on, the agent can hand that one question to Jev and get the
number back.

## Turn it on

**Settings**, **Devices & services**, **Jev**, **Configure**, then **Offer Jev as a
tool to other LLM agents**. It is off by default.

It is off because every LLM agent that uses Assist gets the tool descriptions in every
prompt. That costs tokens at that agent's provider on every turn, whether the agent
calls a tool or not.

## The two tools

| Tool | The agent gives | The agent gets |
|---|---|---|
| `jev__noul` | `question`, optional `facts` | `probability_yes`, 0 to 1 |
| `jev__choice` | `question`, `options` (2 or more), optional `facts` | `choice`, `probabilities`, `confidence` |

Each tool call is a call to [jev.noul or jev.choice](actions.md). It counts against
the same daily budget, and it shows in the same usage sensors.

## What Jev sees

Jev judges the entities exposed to that assistant, the same list the agent itself
reads, up to 150. `facts` carries anything else the question depends on, such as what
the user said.

Jev sends `facts` as it is. The actions render a string `state` as a template, and the
tools do not, because a template could read an entity that you did not expose to
Assist.

## More than one entry

When two or more Jev entries have the option on, each tool has an `account`
parameter, and the agent must name the entry that pays. Each entry has its own key and
its own budget, so Jev does not choose one for the agent.

## The Jev conversation agent

The Jev conversation agent does not use these tools. It asks Jev directly, and it
falls back to another agent for what it cannot route. See
[conversation agent](conversation.md).
