# What it costs

You are billed for **input tokens**, and the text of your questions counts as input.

## The numbers

All measured against the live API from a consumer connection in the Netherlands.

| Thing | Measured |
|---|---|
| One entity record in the state | 65.8 input tokens |
| A short question | about 38 input tokens |
| A spoken command, 5 entities exposed | 1,329 to 1,371 input tokens |
| 30 spoken commands | $0.0017 in total, so $0.000057 each |
| A question preview in the UI | 350 to 876 tokens depending on the target |

At the published $0.042 per million input tokens, the 250 entity cap on a target
works out at roughly 16,500 tokens or **$0.0007** per evaluation.

## Adding questions is nearly free, adding calls is not

| Questions in one request | Time |
|---|---|
| 3 | 712 ms |
| 100 | 714 ms |
| 400 | 1.3 s |

97 extra questions cost 2 ms. A second request costs a whole extra round trip.

So the shape to reach for is one request with everything you might need, discarding
what you do not use. That is what `jev.ask` is for, and what the UI's derived
grouping does for you automatically.

## Where the money actually goes

For a small house, the **questions** dominate. A spoken command with 5 entities
exposed is about 1,350 tokens, of which the seven questions are most of it.

For a large target, the **entities** dominate. At 150 entities you are paying
roughly 110 tokens per entity per call.

`include_attributes` sends every attribute as well. A weather forecast runs to
thousands of tokens on every evaluation, which is why it is off by default. Access
tokens, entity pictures and coordinates are never sent, even with it on.

## The budget is a tripwire

Set **Daily input token budget** in the integration options. The check runs before
the request, not after it: the size of what is about to be sent is measured, turned
into a token estimate, and a call that would not fit in what is left of the budget is
never sent. A budget is a limit on what gets spent, and one that only notices after
the spending is a report.

When it trips:

- the call is refused, so it costs nothing
- the answer sensors of that context go unavailable, because Jev was not asked and
  there is no answer for right now. The last answers are not thrown away, and the
  next call that fits replaces them
- `binary_sensor.jev_daily_budget_exceeded` turns on
- a repair issue explains it, naming the budget and what has been used

It resets at midnight, and the totals survive a restart or a reload, because a daily
budget that either of those cleared would not be a daily budget.

The two one-off paths, a conversation command and the preview in the question editor,
still check what has already been spent rather than estimating the call ahead. Both
are started by a person and both have somewhere to fall back to, so the worst case is
one request over the line rather than a runaway.

### How the estimate is worked out

The estimate is `bytes / bytes-per-token`, with a 1.2 margin. The ratio is not
hardcoded: the first call after a restart uses 2.29 bytes per token, measured against
the live API, and every answered call after that replaces it with the payload size
divided by the input tokens the endpoint actually reported. An endpoint that counts
tokens differently, OpenRouter or a gateway of your own, is measured rather than
assumed within one call. See the
[receipt](measurements.md#bytes-per-input-token).

!!! tip "Size it past anything real"
    A limit you can hit in normal use is the wrong limit. Put it where only a runaway
    reaches it, then leave it alone. If legitimate use touches it, the budget is
    wrong, not your configuration.

## Watching it

| Entity | What it holds |
|---|---|
| `sensor.jev_calls_today` | Requests made today |
| `sensor.jev_input_tokens_today` | Input tokens reported by the API, not an estimate |
| `sensor.jev_estimated_cost_today` | The tokens multiplied by your configured price |
| `binary_sensor.jev_daily_budget_exceeded` | Whether the budget has stopped evaluation |
| `sensor.jev_<context>_payload` | Bytes in the last request that context sent |

The payload sensor is off by default, like the latency sensor beside it. Enable it in
the entity settings when you want it. It holds the same measurement the budget estimate
is worked out from, so it is the number to look at when a context costs more than you
expected. Its attributes carry the exact state that was evaluated and every question as
it was sent, which is what the question preview shows, without opening the editor.
Neither attribute is recorded, because a full request body written to the database
every few minutes is not history anybody wants.

The token count is what the API reported. The money is an estimate, because the price
is a setting here and TypeSafe can change theirs.
