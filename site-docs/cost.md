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
exposed was about 1,350 tokens with seven questions, which were most of it. The
agent asks nine since 1.16.1, 127 tokens more, and twelve since 1.17, about 100
more: 48 for something left out and nothing asked for, 19 for how bright and 33 for
devices named by part of their names. A house with floors is asked one more
question, 63 tokens.

For a large target, the **entities** dominate. At 150 entities you are paying
roughly 110 tokens per entity per call.

`include_attributes` sends every attribute as well. A weather forecast runs to
thousands of tokens on every evaluation, which is why it is off by default. Access
tokens, entity pictures and coordinates are never sent, even with it on.

## A month, worked out

The formula is calls per month, times input tokens per call, times the price. The
examples use the published $0.042 per million input tokens and a 30 day month.

| Setup | Calls a month | Tokens a call | A month |
|---|---|---|---|
| One context on 2 entities, every 5 minutes | 8,640 | about 405 | $0.15 |
| 20 spoken commands a day, 5 entities exposed | 600 | 1,371 at most | $0.035 |
| One context on 250 entities, every 5 minutes | 8,640 | about 16,500 | $5.99 |

Where the token counts come from:

- A request with one entity measured 339 input tokens, and each entity record adds
  65.8, so two entities are about 405. See [what an entity costs](measurements.md#what-an-entity-costs).
- A spoken command with five entities exposed measured 1,329 to 1,371 input tokens.
- 250 entities is the cap on a target, at 65.8 tokens each.

Every 5 minutes is the default scan interval, and it is the most a context on a
schedule asks. A context that wakes on entity changes asks at most once every 30
seconds, so a busy one can ask up to 10 times as often. Your own numbers are in
`sensor.jev_input_tokens_today` and `sensor.jev_estimated_cost_today`.

## The budget is a tripwire

Set **Daily input token budget** in the integration options. The check runs before
the request, not after it: the size of what is about to be sent is measured, turned
into a token estimate, and a call that would not fit in what is left of the budget is
never sent. A budget is a limit on what gets spent, and one that only notices after
the spending is a report.

The estimate is a fixed 250 tokens that every request pays, plus the body size
divided by a bytes-per-token ratio. A question, an action and an AI Task all update
that ratio.

When it trips:

- the call is refused, so it costs nothing
- the answer sensors of that context go unavailable, because Jev was not asked and
  there is no answer for right now. The last answers are not thrown away, and the
  next call that fits replaces them
- `binary_sensor.jev_daily_budget_exceeded` turns on. It shows that contexts have
  stopped, so a spoken command, an action or an AI Task that the budget refuses
  does not turn it on. Each of those says so to whoever started it
- a repair issue explains it, naming the budget, what has been used, and the context
  that was refused with the tokens it needed

Two contexts that ask at the same moment cannot both spend the last of it. A call in
flight holds its estimate against the budget until its answer comes back, so the
second one sees the first.

The connection check at setup is a billed call too. It counts in the day's totals,
and when the budget is already spent, setup skips it.

It resets at midnight in the time zone Home Assistant is set to, not the clock of the
machine it runs on, and the usage sensors show the new day at midnight even when
nothing asks. The estimated cost sensor keeps long-term statistics, one total per
day. The totals survive a restart or a reload, because a daily budget
that either of those cleared would not be a daily budget.

## Calls that are never sent

- A context whose entities are all disabled is not asked. An answer nobody can read
  is still billed.
- A context that wakes on entity changes asks at most once every 30 seconds, however
  often those entities change. The 5 second debounce collects a burst into one call,
  and the 30 seconds is the same floor the scan interval has.
- When TypeSafe answers "too many requests" and says how long to wait, the context
  waits that long before it asks again, even when its scan interval is shorter.
- YAML contexts name no entry, so they belong to the first enabled Jev entry. A
  second entry does not ask them again and bill them twice.

A conversation command, an action and an AI Task estimate their call ahead, like a
context does. The preview in the question editor checks only what has already been
spent. A person starts it and it has nothing to fall back to, so the worst case is one
request over the line rather than a runaway.

### How the estimate is worked out

The estimate is `(250 + bytes / bytes-per-token) * 1.2`. The 250 is what every
request is billed before its body counts. The ratio is not hardcoded: the first call
after a restart uses 2.80 bytes per token, measured against the live API. After that,
every answered call with a body of at least 250 tokens replaces it with what the
endpoint actually billed. A small call leaves it alone, because its bill is nearly all
fixed part. An endpoint that counts tokens differently, OpenRouter or a gateway of
your own, is measured rather than assumed. See the
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
